#!/usr/local/bin/python
# $Id$

"""Legacy hack to evaluate which administrators have access permissions."""

import re

import conf

class ZAuthError(Exception):
    pass

class ZAuth:
    comment_re = re.compile('^\s*(#.*|)$')
    filename = conf.zones_auth
    def __init__(self):
	self._zauth = {}
	self._zauth_email = {}
	for l in open(self.filename, 'r'):
	    if self.comment_re.search(l): continue
	    l = l[:-1]
	    t = l.split(':')
	    if len(t) == 2: t.append(None)
	    if len(t) != 3:
		raise ZAuthError('Invalid line', l)
	    zone, users, email = t
	    zone = zone.upper()
	    self._zauth[zone] = users.split(',')
	    self._zauth_email[zone] = email
    def check(self, zone, user):
	"""Check that a given user has the rights to access a given zone.
	'DNSADMIN' and 'autoreg' have rights on every zone.
	"""
	if user in ['DNSADMIN', 'autoreg']: return True
	zone = zone.upper()
	if zone in self._zauth and user in self._zauth[zone]:
	    return True
	return False
    def checkparent(self, domain, user):
	"""Same as check(), after extracting the zone name from domain.
	"""
	if '.' not in domain:
	    return False
	zone = '.'.join(domain.split('.')[1:])
	return self.check(zone, user)
