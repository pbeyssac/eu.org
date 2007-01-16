#!/usr/local/bin/python
# $Id$

import sre

class ParseError(Exception):
    pass

class DnsParser:
    """Handle minimal zonefile-style line parsing."""
    #
    # Zone file regexps
    #
    # a comment or empty line
    _comment_re = sre.compile('^\s*(;.*|)$')
    # simplified expression for a regular line
    # (does not handle trailing comments, difficult because of string quoting)
    _label_re = sre.compile(
	'^(\S+)?\s+(?:(\d+)\s+)?(?:[Ii][Nn]\s+)?(\S+)\s+(\S|\S.*\S)\s*$')
    # right-hand side for a MX record
    _mx_re = sre.compile('^(\d+)\s+(\S+)$')
    # lines such as $TTL ...
    _dollar_re = sre.compile('^\$(\S+)\s+(\d+)\s*$')
    # SOA lines
    _soa_begin_re = sre.compile('^.*\(\s*(?:;.*)?$')
    _soa_end_re = sre.compile('^\s+\d+\s*\)\s*(?:;.*)?$')

    def __init__(self):
        self.insoa = False
    def parseline(self, l):
	if self._comment_re.search(l): return None
	if self._dollar_re.search(l): return None
        if self.insoa:
            if self._soa_end_re.search(l):
                self.insoa = False
            return None
	m = self._label_re.search(l)
	if not m: raise ParseError('Unable to parse line', l)
	label, ttl, typ, value = m.groups()
	if label == None:
	    label = ''
	else:
	    label = label.upper()
	typ = typ.upper()
	# Do some quick & dirty checking and canonicalization
	if typ in ['AAAA', 'CNAME', 'NS', 'SRV']:
	    value = value.upper()
	elif typ == 'MX':
	    m = self._mx_re.search(value)
	    if not m: raise ParseError('Bad value for MX record', value)
	    pri, fqdn = m.groups()
	    pri = int(pri)
	    if pri > 255: raise ParseError('Bad priority for MX record', pri)
	    value = "%d %s" % (pri, fqdn.upper())
	elif typ in ['A', 'TXT', 'PTR']:
	    pass
        elif typ == 'SOA':
            if self._soa_begin_re.search(value.strip()):
                self.insoa = True
	else:
	    raise ParseError('Illegal record type', typ)
	return (label, ttl, typ, value)
