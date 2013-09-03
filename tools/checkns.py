#!/usr/local/bin/python
# $Id$
#
# Gets on stdin :
# 1)    a domain name
# 2)    lines giving, for each server, its fqdn and
#       (optionally) its IP address.
#
#       -- OR --
#
# Gets a domain name in argument then retrieves the NS list on the Internet.
#
# Checks :
# 1)    IP addresses of servers not in domain
# 2)    primary and secondaries are authoritative for the domain
# 3)    NS records for domain on all listed server match the provided list.
#

import re
import socket
import sys

import dns
import dns.ipv6
import dns.message
import dns.query
import dns.rdatatype
import dns.resolver

def sendquery(q, server):
  trytcp = False
  try:
    r = dns.query.udp(q, server, timeout=10)
  except dns.query.BadResponse:
    return None, "BadResponse"
  except dns.query.UnexpectedSource:
    return None, "UnexpectedSource"
  except dns.exception.Timeout:
    trytcp = True
  except socket.error, e:
    return None, e

  if not trytcp:
    return True, r

  try:
    r = dns.query.tcp(q, server, timeout=10)
  except dns.query.BadResponse:
    return None, "BadResponse"
  except dns.query.UnexpectedSource:
    return None, "UnexpectedSource"
  except dns.exception.Timeout:
    return None, "Timeout"
  except socket.error, e:
    return None, e

  return True, r

def getsoa(qsoa, server):
  """Send SOA query to server and wait for reply.
     Return master name and serial.
  """
  ok, r = sendquery(qsoa, server)
  if not ok:
    return None, r
  if (r.flags & dns.flags.AA) == 0:
    return None, "Answer not authoritative"
  if len(r.answer) == 0:
    return None, "Empty answer"
  if len(r.answer) != 1:
    return None, "Unexpected answer length"
  if len(r.answer[0].items) != 1:
    return None, "Unexpected number of items"
  if r.answer[0].items[0].rdtype != dns.rdatatype.SOA:
    return None, "Answer type mismatch"
  mastername = r.answer[0].items[0].mname.__str__().upper()
  serial = r.answer[0].items[0].serial
  return True, (mastername, serial)

def getnslist(domain, server):
  qns = dns.message.make_query(domain, 'NS')
  qns.flags = 0
  ok, r = sendquery(qns, server)
  if not ok:
    return None, r
  if (r.flags & dns.flags.AA) == 0:
    return None, "Answer not authoritative"
  if len(r.answer) == 0:
    return None, "Empty answer"
  if len(r.answer) != 1:
    return None, "Unexpected answer length"
  nslist = []
  for a in r.answer[0].items:
    v = a.to_text().upper()
    if v.endswith('.'):
      v = v[:-1]
    nslist.append(v)
  nslist.sort()
  return True, nslist

def main():
  tcp=False
  errs = 0
  warns = 0
  fqdnlist = []
  r = dns.resolver.Resolver()

  print "---- Servers and domain names check"
  print

  manualip = { }

  if len(sys.argv) == 2:
    domain = sys.argv[1].upper()
    #
    # Fetch NS list from public DNS
    #
    print "Querying NS list for", domain, "...",
    try:
      ans = r.query(domain, 'NS', tcp=tcp)
    except dns.resolver.NXDOMAIN:
      print "Error: Domain not found"
      sys.exit(1)
    except dns.exception.Timeout:
      print "Error: Timeout"
      sys.exit(1)
    except dns.resolver.NoAnswer:
      print "Error: No answer"
      sys.exit(1)
    except dns.resolver.NoNameservers:
      print "Error: No name servers"
      sys.exit(1)
    print len(ans.rrset.items), "records"
    print
    for i in ans.rrset.items:
      fqdn = i.to_text().upper()
      if fqdn.endswith('.'):
        fqdn = fqdn[:-1]
      fqdnlist.append(fqdn)
  else:
    #
    # Fetch domain and NS list from stdin
    #
    fqdnip = re.compile('^([a-zA-Z0-9\.-]+)(?:\s+(\S+))?\s*$')
    domain = sys.stdin.readline()
    domain = domain[:-1].upper()
    for l in sys.stdin:
      l = l[:-1]
      m = fqdnip.match(l)
      if not m:
        print "Error: Invalid line"
        errs += 1
        continue
      fqdn, ip = m.groups()
      if ip is not None:
        ip = ip.upper()

        try:
          if ':' in ip:
            socket.inet_pton(socket.AF_INET6, ip)
          else:
            socket.inet_pton(socket.AF_INET, ip)
        except socket.error:
          print "Error: Invalid IP address", ip
          ip = None
          errs += 1

      if ip is not None:
        if fqdn.endswith('.'+domain):
          manualip[fqdn] = ip
        else:
          print "Error: don't specify IP %s for %s (not in %s)" % (ip, fqdn, domain)
          errs += 1

      fqdn = fqdn.upper()
      fqdnlist.append(fqdn)

    if fqdnlist:
      mastername = fqdnlist[0]

  if not domain:
    print "Error: no domain specified"
    errs += 1
  if not fqdnlist:
    print "Error: empty name server list"
    errs += 1

  if errs:
    print errs, "errors(s)",
    sys.exit(1)

  fqdnlist.sort()

  if domain.startswith('.'):
    domain = domain[1:]
  domaindot = domain
  if domain.endswith('.'):
    domain = domain[:-1]
  else:
    domaindot += '.'

  #
  # Build IP address list
  #
  ips = []
  for fqdn in fqdnlist:
    if fqdn.endswith('.'):
      fqdn = fqdn[:-1]

    if fqdn in manualip:
      print "Accepted IP for %s: %s" % (fqdn, manualip[fqdn])
      continue

    print "Getting IP for %s..." % fqdn,
    n = 0
    for t in ['A', 'AAAA']:
      try:
        aip = r.query(fqdn, t, tcp=tcp)
      except dns.resolver.NXDOMAIN:
        continue
      except dns.exception.Timeout:
        continue
      except dns.resolver.NoAnswer:
        continue
      except dns.resolver.NoNameservers:
        continue
      for iprr in aip.rrset.items:
        print iprr.to_text(),
        ips.append((fqdn, t, iprr.to_text()))
        n += 1
    if n == 0:
      print "FAILED",
      errs += 1
    print

  if errs:
    print errs, "errors(s)",
    sys.exit(1)

  qsoa = dns.message.make_query(domain, 'SOA')
  qsoa.flags = 0
  mastername = None
  serial = None

  print
  print "---- Checking SOA & NS records for", domain
  print

  for fqdnip in ips:
    fqdn, t, i = fqdnip
    print "Getting SOA from %s at %s..." % (fqdn, i),
    ok, soa = getsoa(qsoa, i)
    if not ok:
      print "Error:", soa
      errs += 1
    else:
      print "serial", soa[1]
    print "Getting NS from %s at %s..." % (fqdn, i),
    ok, nslist = getnslist(domain, i)
    if not ok:
      print "Error:", nslist
      errs += 1
    elif nslist != fqdnlist:
      if mastername and mastername == fqdn:
        print "Error: Bad NS list", nslist
        errs += 1
      else:
        print "Warning: Bad NS list", nslist
        warns += 1
    else:
      print "ok"

  if errs or warns:
    print
  if errs:
    print errs, "errors(s)",
  if warns:
    print warns, "warning(s)",
  print
  if errs:
    sys.exit(1)
  sys.exit(0)

if __name__ == "__main__":
  main()
