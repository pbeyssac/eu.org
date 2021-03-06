#
# DNS checks
#

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import datetime
import re
import socket
import sys
import time

import dns
import dns.message
import dns.query
import dns.rdatatype
import dns.resolver

import autoreg.conf

#
# Import Django i18n framework, if available and configured.
# If not available, make _() a noop.
#
try:
  from django.utils.translation import ugettext as _
  from django.core.exceptions import ImproperlyConfigured
except ImportError:
  def _(string):
    return string

try:
  a = _("Save")
except ImproperlyConfigured:
  def _(string):
    return string


def checkip(ip):
  """Check IPv4/IPv6 address for validity"""
  try:
    if ':' in ip:
      socket.inet_pton(socket.AF_INET6, ip)
    else:
      socket.inet_pton(socket.AF_INET, ip)
  except socket.error:
    return False
  return True

#
# Regexp for a valid FQDN:
#  - only letters, digits, '-'
#  - no '-' at the beginning or end of a label
#  - at least one '.'
#  - no '.' at the beginning or end
#
_valid_fqdn = re.compile('^(?:[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?\.)+'
                             '[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?$',
                         re.IGNORECASE)
# Internal FQDN: same but allow _ in addition to letters.
_internal_valid_fqdn = re.compile('^(?:[_A-Z0-9](?:[_A-Z0-9-]*[_A-Z0-9])?\.)+'
                                  '[_A-Z0-9](?:[_A-Z0-9-]*[_A-Z0-9])?$',
                         re.IGNORECASE)

def checkfqdn(fqdn):
  """Check fully-qualified domain name for validity"""
  return _valid_fqdn.match(fqdn)

def checkinternalfqdn(fqdn):
  """Check fully-qualified internal domain name for validity"""
  return _internal_valid_fqdn.match(fqdn)

def sendquery(q, server, timeout=10):
  """Send DNS query q, in UDP then TCP, to server"""
  trytcp = False
  try:
    r = dns.query.udp(q, server, timeout=timeout)
  except dns.query.BadResponse:
    return None, _("BadResponse")
  except dns.query.UnexpectedSource:
    return None, _("UnexpectedSource")
  except dns.exception.Timeout:
    trytcp = True
  except socket.error as e:
    return None, e

  if not trytcp:
    return True, r

  try:
    r = dns.query.tcp(q, server, timeout=timeout)
  except dns.query.BadResponse:
    return None, _("BadResponse")
  except dns.query.UnexpectedSource:
    return None, _("UnexpectedSource")
  except EOFError:
    return None, _("Dropped connection")
  except dns.exception.Timeout:
    return None, _("Timeout")
  except socket.error as e:
    return None, e

  return True, r

def undot_list(fqdnlist):
  return [fqdn.rstrip('.') for fqdn in fqdnlist]


class MultiResolver(object):
  def __init__(self, domain, nslist=[], manualip={}, nat={}, timeout=10):
    self.res = dns.resolver.Resolver()
    self.domain = domain
    self.mastername = None
    self.manualip = manualip
    self.nat = nat
    self.timeout = timeout
    if not domain.endswith('.'):
      domain += '.'
    qns = dns.message.make_query(domain, 'NS')
    qns.flags = 0
    self.qns = qns
    if nslist:
      self.setnslist_direct(nslist)
      self.resolve_ips()

  def getnslist(self, server):
    """Send NS query to server and wait for reply.
       Return NS list.
    """
    t1 = time.time()
    ok, r = sendquery(self.qns, server, timeout=self.timeout)
    t = time.time()
    t = (t - t1)*1000
    if not ok:
      return None, r, t
    if (r.flags & dns.flags.AA) == 0:
      return None, _("Answer not authoritative"), t
    if len(r.answer) == 0:
      return None, _("Empty answer"), t
    if len(r.answer) != 1:
      return None, _("Unexpected answer length"), t
    nslist = [a.to_text().upper() for a in r.answer[0].items]
    nslist = undot_list(nslist)
    nslist.sort()
    return True, nslist, t

  def gettxt(self, server, domain):
    q = dns.message.make_query(domain, 'TXT')
    q.flags = 0
    t1 = time.time()
    ok, r = sendquery(q, server, timeout=self.timeout)
    t = time.time()
    t = (t - t1)*1000
    if not ok:
      return None, r, t
    if (r.flags & dns.flags.AA) == 0:
      return None, _("Answer not authoritative"), t
    if len(r.answer) == 0:
      return None, _("Empty answer"), t
    if len(r.answer) != 1:
      return None, _("Unexpected answer length"), t
    if len(r.answer[0]) != 1:
      return None, _("Unexpected answer content length"), t
    txt = r.answer[0][0].to_text()
    return True, txt, t

  def getany_domain(self, server, domain):
    q = dns.message.make_query(domain, 'ANY')
    q.flags = 0
    t1 = time.time()
    ok, r = sendquery(q, server, timeout=self.timeout)
    t = time.time()
    t = (t - t1)*1000
    if not ok:
      return None, r, t
    rc = r.rcode()
    if rc == 0:
      return True, _("Domain exists"), t
    if rc == dns.rcode.NXDOMAIN:
      return None, "NXDOMAIN", t
    return None, _("Unexpected answer"), t

  def setnslist_direct(self, nslist):
    """Set NS list from provided list"""
    nslist.sort()
    self.nslist = undot_list(nslist)
    return True, self.nslist

  def setnslist_public(self):
    """Fetch NS list from resolver"""
    tcp = False
    nslist = []
    try:
      ans = self.res.query(self.domain+'.', 'NS', tcp=tcp)
    except dns.resolver.NXDOMAIN:
      return None, _("Domain not found")
    except dns.exception.Timeout:
      return None, _("Timeout")
    except dns.resolver.NoAnswer:
      return None, _("No answer")
    except dns.resolver.NoNameservers:
      return None, _("No name servers")
    for i in ans.rrset.items:
      fqdn = i.to_text().upper()
      nslist.append(fqdn)
    self.setnslist_direct(nslist)
    return True, self.nslist

  def setnslist_file(self, file, checkglue):
    """Fetch NS list from file"""
    errlist = []
    nsiplist = []
    fqdnip = re.compile('^([a-zA-Z0-9\.-]+)(?:\s+(\S+))?\s*$')
    for l in file:
      l = l[:-1]
      m = fqdnip.match(l)
      if not m:
        errlist.append(_("Invalid line"))
        continue
      fqdn, ip = m.groups()
      nsiplist.append((fqdn, ip))
    return self.setnslist_nsiplist(nsiplist, checkglue, errlist)

  def setnslist_nsiplist(self, nsiplist, checkglue, errlist=None):
    """Fetch NS list from (fqdn, ip) list"""
    errlist = errlist or []
    warnlist = []
    nslist = []
    for fqdn, ip in nsiplist:
      fqdn = fqdn.upper()
      if ip:
        ip = ip.upper()

        if not checkip(ip):
          errlist.append(_("Invalid IP address %s") % ip)
          ip = None

      if fqdn.endswith('.'+self.domain.upper()) \
         or fqdn == self.domain.upper():
        if ip:
          if fqdn not in self.manualip:
            self.manualip[fqdn] = []
          self.manualip[fqdn].append(ip)
        elif checkglue:
          errlist.append(_("Missing glue IP for %s") % fqdn)
      elif ip:
        warnlist.append(_("ignoring IP %(ip)s"
                        " for %(fqdn)s (not in %(domain)s)")
                        % {'ip': ip, 'fqdn': fqdn, 'domain': self.domain})

      if fqdn not in nslist:
        nslist.append(fqdn)
    if nslist:
      self.mastername = nslist[0]
    self.setnslist_direct(nslist)
    return errlist, warnlist

  def gen_ips(self):
    """Resolve ips from self.nslist.
    Take manualip and nat tables into account.
    """
    tcp = False
    for fqdn in self.nslist:
      fqdn = fqdn.upper()
      if fqdn in self.manualip:
        yield True, (False, fqdn, self.manualip[fqdn])
        continue
      n = 0
      for t in ['A', 'AAAA']:
        try:
          aip = self.res.query(fqdn, t, tcp=tcp)
        except dns.resolver.NoAnswer:
          continue
        except dns.resolver.NXDOMAIN:
          continue
        except dns.exception.Timeout:
          continue
        except dns.resolver.NoNameservers:
          continue
        iplist = [ip.to_text() for ip in aip.rrset.items]
        natlist = []
        for ip in iplist:
          if ip in self.nat:
            natlist.append(self.nat[ip])
          else:
            natlist.append(ip)
        yield True, (True, fqdn, natlist)
        n += 1
      if n == 0:
        yield None, (None, fqdn, [])

  def gen_ns(self):
    """Get NS records in turn from each IP in self.ips."""
    for resolved, fqdn, iplist in self.ips:
      for i in iplist:
        ok, r, t = self.getnslist(i)
        yield ok, fqdn, i, r, t

  def gen_resolve_ips(self):
    """Resolve IPs, storing the result in self.ips."""
    ips = []
    for ok, resolved_fqdn_ip in self.gen_ips():
      resolved, fqdn, ip = resolved_fqdn_ip
      ips.append(resolved_fqdn_ip)
      if not ok:
        yield None, _("Getting IP for %s: FAILED") % fqdn,
      elif resolved:
        yield True, _("Getting IP for %(fqdn)s: %(ip)s") \
                      % {'fqdn': fqdn, 'ip': ' '.join(ip)}
      else:
        yield True, _("Accepted IP for %(fqdn)s: %(ip)s") \
                      % {'fqdn': fqdn, 'ip': ' '.join(ip)}
    self.ips = ips

  def resolve_ips(self):
    """Resolve IPs, storing the result in self.ips."""
    for ok, msg in self.gen_resolve_ips():
      pass

LEVEL_IP = 1
LEVEL_SOA = 2
LEVEL_NS = 3

class SOAChecker(MultiResolver):
  def __init__(self, domain, nslist=[], manualip={}, nat={}, timeout=10, dbh=None):
    super(self.__class__, self).__init__(domain, nslist, manualip, nat, timeout=timeout)
    if not domain.endswith('.'):
      domain += '.'
    self.domain = domain[:-1].upper()
    qsoa = dns.message.make_query(domain, 'SOA')
    qsoa.flags = 0
    self.qsoa = qsoa
    self.level = LEVEL_NS
    self.serial_log = []
    self.dbh = dbh

  def set_level(self, level):
    """LEVEL_IP: check FQDNs/IP only
       LEVEL_SOA: + check SOA
       LEVAL_NS: + check NS
    """
    self.level = level

  def getsoa(self, server, fqdn=None):
    """Send SOA query to server and wait for reply.
       Return master name and serial.
    """
    t1 = time.time()
    ok, r = sendquery(self.qsoa, server, timeout=self.timeout)
    t2 = time.time()
    t = (t2 - t1)*1000
    if not ok:
      return None, r, t
    if (r.flags & dns.flags.AA) == 0:
      return None, _("Answer not authoritative"), t
    if len(r.answer) == 0:
      return None, _("Empty answer"), t
    if len(r.answer) != 1:
      return None, _("Unexpected answer length"), t
    if len(r.answer[0].items) != 1:
      return None, _("Unexpected number of items"), t
    if r.answer[0].items[0].rdtype != dns.rdatatype.SOA:
      return None, _("Answer type mismatch"), t
    mastername = str(r.answer[0].items[0].mname).upper()
    serial = r.answer[0].items[0].serial
    self.serial_log.append((self.domain, fqdn, server, serial, t2))
    return True, (mastername, serial), t

  def gen_soa(self):
    """Get SOA in turn from each IP in self.ips."""
    for resolved, fqdn, iplist in self.ips:
      for i in iplist:
        ok, r, t = self.getsoa(i, fqdn.lower())
        yield ok, fqdn, i, r, t

  def print_checks(self):
    """Run gen_soa() and gen_ns(), displaying messages as we go."""
    serials = {}

    yield True, ""

    if self.level < LEVEL_SOA:
      return

    yield True, _("---- Checking SOA records for %s") % self.domain
    yield True, ""

    for ok, fqdn, i, r, t in self.gen_soa():
      if not ok:
        yield None, _("SOA from %(fqdn)s at %(ip)s: Error: %(err)s (%(t).3f ms)") \
                     % {'fqdn': fqdn, 'ip': i, 'err': r, 't' :t}
      else:
        if r[1] in serials:
          serials[r[1]].append(i)
        else:
          serials[r[1]] = [i]
        yield True, _("SOA from %(fqdn)s at %(ip)s: serial %(serial)s (%(t).3f ms)") \
                     % {'fqdn': fqdn, 'ip': i, 'serial': r[1], 't' :t}
    if serials and len(serials) > 1:
      serialsk = list(serials.keys())
      serialsk.sort()
      if serialsk[-1] - serialsk[0] < (1<<31):
        # no serial number wraparound
        del serials[serialsk[-1]]
        values = []
        for f in serials.values():
          values.extend(f)
        yield True, _("Servers not up to date: ") + ' '.join(values)
      else:
        yield True, _("Some servers are not up to date!")

    yield True, ""

    if self.level < LEVEL_NS:
      return

    yield True, _("---- Checking NS records for %s") % self.domain
    yield True, ""

    for ok, fqdn, i, r, t in self.gen_ns():
      if not ok:
        yield None, _("NS from %(fqdn)s at %(ip)s: Error: %(err)s (%(t).3f ms)") \
                      % {'fqdn': fqdn, 'ip': i, 'err': r, 't': t}
      elif r != self.nslist:
        yield None, _("NS from %(fqdn)s at %(ip)s: Error: Bad NS list: %(err)s (%(t).3f ms)") \
                      % {'fqdn': fqdn, 'ip': i, 'err': ' '.join(r), 't': t}
      else:
        yield True, _("NS from %(fqdn)s at %(ip)s: ok (%(t).3f ms)") \
                      % {'fqdn': fqdn, 'ip': i, 't': t}
    yield True, ""

  def main(self, file=None, nsiplist=None, checkglue=True):
    """Main processing to check a list of servers for a given zone.
    Checks:
    1)    IP addresses of servers not in domain
    2)    primary and secondaries are authoritative for the domain
    3)    NS records for domain on all listed server match the provided list.
    """
    self.errs = 0
    self.warns = 0

    yield True, _("---- Servers and domain names check")
    yield True, ""

    if self.domain.startswith('.'):
      self.domain = self.domain[1:]
    if self.domain.endswith('.'):
      self.domain = self.domain[:-1]

    if not file and not nsiplist:
      #
      # Fetch NS list from public DNS
      #
      ok, r = self.setnslist_public()
      if not ok:
        yield None, _("Querying NS list for %(fqdn)s... %(err)s") \
                      % {'fqdn': self.domain, 'err': r}
        yield None, r
        self.errs += 1
        return
      yield True, _("Querying NS list for %(fqdn)s... %(len)d records") \
            % {'fqdn': self.domain, 'len': len(self.nslist)}
      yield True, ""

    else:
      #
      # Fetch NS list from file or list
      #
      if nsiplist:
        errlist, warnlist = self.setnslist_nsiplist(nsiplist, checkglue)
      else:
        errlist, warnlist = self.setnslist_file(file, checkglue)

      for e in errlist:
        yield None, _("Error: ") + e
      self.errs += len(errlist)
      for e in warnlist:
        yield True, _("Warning: ") + e
      self.warns += len(warnlist)

    if not self.nslist:
      yield True, _("Error: ") + _("Empty name server list")
      self.errs += 1

    if self.errs:
      yield None, _("%s errors(s)") % self.errs
      return

    #
    # Build IP address list
    #

    for ok, msg in self.gen_resolve_ips():
      yield True, msg
      if not ok:
        self.errs += 1

    if self.errs:
      yield None, _("%d errors(s)") % self.errs

    if not self.ips:
      yield None, _("No IP address found, exiting")
      return

    for ok, msg in self.print_checks():
      yield True, msg
      if not ok:
        self.errs += 1

    if self.dbh:
      for ok, msg in handle_serial_stats(self.domain, self.serial_log, self.dbh):
        yield True, msg
        if not ok:
          self.errs += 1

    if self.errs or self.warns:
      yield True, ""
    if self.errs:
      yield None, _("%d errors(s)") % self.errs
    if self.warns:
      yield True, _("%d warning(s)") % self.warns,
    yield True, ""


class DNSKEYChecker(MultiResolver):
  def __init__(self, domain, nslist=[], manualip={}, nat={}, timeout=10):
    super(self.__class__, self).__init__(domain, nslist, manualip, nat, timeout=timeout)
    if not domain.endswith('.'):
      domain += '.'
    qdnskey = dns.message.make_query(domain, 'DNSKEY')

    # CD: accept replies even if domain is broken, so we can obtain
    # the records anyway
    qdnskey.flags = dns.flags.CD|dns.flags.RD
    qdnskey.use_edns(0, 0, 1500)
    self.qdnskey = qdnskey

  def getdnskey(self, server):
    """Send DNSKEY query to server and wait for reply.
       Return answer.
    """
    ok, r = sendquery(self.qdnskey, server, timeout=self.timeout)
    if not ok:
      return []
    if (r.flags & dns.flags.AA) == 0:
      return []
    if len(r.answer) == 0:
      return []
    if len(r.answer) != 1:
      return []
    return r.answer[0]

  def getalldnskey(self):
    dnskey = []
    for resolved, fqdn, iplist in self.ips:
      for i in iplist:
        for newkey in self.getdnskey(i):
          # filter out CNAMEs & others
          if newkey.rdtype == dns.rdatatype.DNSKEY and newkey not in dnskey:
            dnskey.append(newkey)
    return dnskey


def main(argv=sys.argv, infile=sys.stdin, outfile=sys.stdout, dbh=None):
  """Gets on stdin :
  1)    a domain name
  2)    lines giving, for each server, its fqdn and
        (optionally) its IP address.

        -- OR --

  Gets a domain name in argument then retrieves the NS list on the Internet.

  Then proceeds with SOAChecker.main().
  """
  errs = 0
  fqdnlist = []
  manualip = { }
  nat = { }
  checkglue = True

  import getopt

  try:
    optlist, args = getopt.getopt(argv[1:], 'go:')
  except getopt.GetoptError as err:
    print(str(err), file=outfile)
    return 2

  for opt, val in optlist:
    if opt == '-o':
      oldip, newip = val.split('=')
      for ip in oldip, newip:
        if not checkip(ip):
          print(_("Error: ") + _("Invalid IP address %s") % ip, file=outfile)
          errs += 1
          ip = None
      if ip is not None:
        nat[oldip] = newip
    if opt == '-g':
        checkglue = False

  if len(args) == 1:
    domain = args[0]
  else:
    # Fetch domain from infile (default stdin)
    domain = infile.readline()
    domain = domain[:-1]

  soac = SOAChecker(domain, manualip, nat, dbh=dbh)

  for ok, out in soac.main(file=infile, checkglue=checkglue):
    print(out, file=outfile)
    if not ok:
      errs += 1

  if errs:
    return 1
  return 0


def find_last_serial(serials):
  """Try to find the likeliest latest serial."""
  while len(serials) > 1:
    s1 = serials[0]
    s2 = serials[1]
    d = s1 - s2
    if d < 0:
      d += 2 ** 32
    if d < 2 ** 31:
      # remove s2
      serials = [serials[0]] + serials[2:]
    else:
      # remove s1
      serials = serials[1:]
  return serials[0] if serials else None


def handle_serial_stats(zone, stats, dbh):
  """Handle serial stats: store in database, emit warnings if necessary."""
  dbc = dbh.cursor()
  for s in stats:
    d = datetime.datetime.fromtimestamp(s[4], datetime.timezone.utc)
    dbc.execute('INSERT INTO serial_server_log (zone, fqdn, ip, serial, first_seen, last_seen)'
      ' VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT(zone, ip, serial) DO UPDATE SET last_seen = %s',
      (s[0], s[1], s[2], s[3], d, d, d))
    dbh.commit()

  serials = [s[3] for s in stats]
  slatest = find_last_serial(serials)
  serials = [s for s in serials if s != slatest]

  dbc.execute(
     'SELECT serial, MIN(min) FROM serial_log WHERE zone=%(zone)s AND serial=ANY(%(serials)s)'
     ' GROUP BY serial ORDER BY min LIMIT 1',
     {'zone': zone, 'serials': serials})
  dnow = datetime.datetime.now()
  for smin, dmin in dbc.fetchall():
    if dnow - dmin <= datetime.timedelta(seconds=autoreg.conf.MAX_ZONE_AGE):
      yield True, (_("Zone %(zone)s: outdated copy (serial %(smin)d, dated %(dmin)s)")
                    % {'zone': zone, 'smin': smin, 'dmin': dmin})
  for s in stats:
    if s[3] != slatest:
      yield None, (_("Server %(fqdn)s (%(ip)s): %(zone)s serial %(serial)s")
                    % {'fqdn': s[1], 'ip': s[2], 'zone': zone, 'serial': s[3]})

  return


def main_checkallsoa():
  return checkallsoa(sys.argv)

def checkallsoa(argv, file=sys.stdout):
  import io
  import os

  import psycopg2

  import autoreg.dns.db

  if len(argv) > 1:
    domlist = [dom.upper() for dom in argv[1:]]
  else:
    domlist = None

  dbh = psycopg2.connect(autoreg.conf.dbstring)
  dd = autoreg.dns.db.db(dbc=dbh.cursor(), nowrite=True)
  user = os.getenv('USER', None)
  dd.login(user)

  exitcode = 0
  for zone in dd.zonelist():
    if domlist and zone not in domlist:
      continue
    infile = io.StringIO()
    for t in dd.get_ns(zone, zone, zoneglue=True):
        if t[2]:
          print(t[0], t[2], file=infile)
        else:
          print(t[0], file=infile)
    infile.seek(0)
    outfile = io.StringIO()
    r = main(argv=['check-ns', '-g', zone], infile=infile, outfile=outfile, dbh=dbh)

    if r:
      print('****', zone, 'FAILED ****', file=file)
      print(outfile.getvalue(), end='', file=file)
      exitcode = 1

  return exitcode

if __name__ == "__main__":
  sys.exit(main())
