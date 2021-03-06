from __future__ import absolute_import
from __future__ import unicode_literals
from __future__ import print_function


import re


from django.db import connection
from django.test import TestCase, Client


import autoreg.dns.db
from autoreg.whois.db import suffixadd
from ..requests.models import Requests
from ..whois.models import Admins, Contacts, DomainContact, Whoisdomains
from .models import Domains, Zones


class DomainNewTest(TestCase):
  def setUp(self):
    self.pwtu1 = 'abcdefgh'
    cursor = connection.cursor()

    # Minimal test account
    self.handle = suffixadd('TP1')
    self.pw = 'aaabbbcccddd'

    # Admin account
    self.admin_handle = suffixadd('AA1')
    self.pw3 = 'aaabbbcccddd3'
    a = Admins.objects.get(login='AA1')

    z = Zones.objects.get(name='EU.ORG')
    self.zone_id = z.id

    Domains(name='ORPHAN', zone=z, created_by=a, updated_by=a).save()
    Domains(name='FOOBAR', zone=z, created_by=a, updated_by=a).save()

    self.domain = 'foobar.eu.org'
    w = Whoisdomains(fqdn=self.domain.upper())
    w.save()
    DomainContact(whoisdomain=w, contact=Contacts.objects.get(handle='TP1'), contact_type_id=1).save()

    self.c = Client()

  def test_domain_new_get_1(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.get('/en/domain/new/')
    self.assertEqual(200, r.status_code)
    self.assertTrue(b'value="Test Person"' in r.content)

  def test_domainns_th_length(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))

    fields = {
      'fqdn': 'BLABLA.EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'th': suffixadd('TP1XXXXXXXXXXXXXXXXXXXXXXXXXXX'),
      'level': '1',
      'f1': 'NS.EU.ORG',
      'i1': ''
    }
    r = self.c.post('/en/domain/new/', fields)
    c = str(r.content)
    self.assertEqual(200, r.status_code)
    self.assertTrue('class="fieldWrappererror">\\n    <input name="th"' in c)

    fields['th'] = suffixadd('TP2')
    r = self.c.post('/en/domain/new/', fields)
    c = str(r.content)
    self.assertEqual(200, r.status_code)
    self.assertTrue('class="fieldWrappererror">\\n    <input name="th"' in c)

    fields['th'] = suffixadd('ZZ1111')
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue(hasattr(r, 'streaming_content'))

  def test_domainns_request_1(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    fields = {
      'fqdn': 'BLABLA.EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'th': suffixadd('TP1'),
      'level': '1',
      'f1': 'NS.EU.ORG',
      'i1': ''
    }
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue(hasattr(r, 'streaming_content'))
    for line in r.streaming_content:
      pass

    r = Requests.objects.get(contact__handle='TP1')
    self.assertEqual("\tNS\tNS.EU.ORG.\n", r.zonerecord)

  def test_domainns_request_2(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    fields = {
      'fqdn': 'BLABLA.EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'th': suffixadd('TP1'),
      'level': '1',
      'f1': 'BLABLA.EU.ORG',
      'i1': '192.168.0.1',
      'f2': 'NS.BLABLA.EU.ORG',
      'i2': '192.168.0.2'
    }
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue(hasattr(r, 'streaming_content'))
    for line in r.streaming_content:
      pass
    r = Requests.objects.get(contact__handle='TP1')
    self.assertEqual("\tNS\tBLABLA.EU.ORG.\n"
                     "\tNS\tNS.BLABLA.EU.ORG.\n"
                      "\tA\t192.168.0.1\n"
                      "NS\tA\t192.168.0.2\n",
                     r.zonerecord)

  def test_domainns_request_3(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    fields = {
      'fqdn': 'BLABLA..EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'th': suffixadd('TP1'),
      'level': '1',
      'f1': 'BLABLA.EU.ORG',
      'i1': '192.168.0.1',
      'f2': 'NS.BLABLA.EU.ORG',
      'i2': '192.168.0.2'
    }
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Invalid domain name' in str(r.content))

  def test_domainns_changens_1(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    fields = {
      'fqdn': 'FOOBAR.EU.ORG',
      'level': '1',
      'f1': 'NS1.FOOBAR.EU.ORG',
      'i1': '192.168.0.1',
      'f2': 'NS2.FOOBAR.EU.ORG',
      'i2': '192.168.0.2'
    }
    r = self.c.post('/en/ns/foobar.eu.org/' , fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue(hasattr(r, 'streaming_content'))
    for line in r.streaming_content:
      pass

    fields = {
      'fqdn': 'FOOBAR.EU.ORG',
      'level': '1',
      'f1': 'NS3.FOOBAR.EU.ORG',
      'i1': '192.168.0.1',
      'f2': 'NS4.FOOBAR.EU.ORG',
      'i2': '192.168.0.2'
    }
    r = self.c.post('/en/ns/foobar.eu.org/' , fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue(hasattr(r, 'streaming_content'))
    for line in r.streaming_content:
      pass

  def test_domainns_orphan_ok(self):
    self.assertTrue(self.c.login(username='AA1', password=self.pw3))

    fields = {
      'fqdn': 'ORPHAN.EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'orphan': '1',
      'th': suffixadd('TP1'),
      'level': '1'
    }
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Object created:' in str(r.content))
    self.assertTrue('Whois input:\\n<pre>\\ndomain:' in str(r.content))
    self.assertEqual(1, len(Whoisdomains.objects.filter(fqdn='ORPHAN.EU.ORG')))

  def test_domainns_orphan_forbidden(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))

    fields = {
      'fqdn': 'ORPHAN.EU.ORG',
      'pn1': 'John Snow',
      'ad1': 'The North',
      'ad2': '59000 Lenord',
      'ad6': 'FR',
      'private': 'on',
      'orphan': '1',
      'th': suffixadd('TP1'),
      'level': '1'
    }
    r = self.c.post('/en/domain/new/', fields)
    self.assertEqual(200, r.status_code)
    self.assertFalse('Object created:' in str(r.content))
    self.assertEqual(0, len(Whoisdomains.objects.filter(fqdn='ORPHAN.EU.ORG')))

  def test_domainds_get_ko(self):
    r = self.c.get('/en/ds/eu.org/', {})
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/login/?next=/en/ds/eu.org/', r['Location'])
  def test_domainds_post_ko_1(self):
    r = self.c.post('/en/ds/eu.org/', {})
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/login/?next=/en/ds/eu.org/', r['Location'])
  def test_domainds_post_ko_2(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.post('/en/ds/nons.dnssec.tests.eu.org/', {})
    self.assertEqual(403, r.status_code)
  def test_domainds_post_ko_3(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      # old expired EU.ORG key
      'rr': 'EU.ORG. DNSKEY 256 3 8 AwEAAaD28FYK/09FqyUtDaG0ZnkoB7rarNe5I70Ll1/ZDRlcSCDBffa6 Pr/Za1/MCkTa1DN8ZbGKlk1oXKde081tuRmdmIUpibvLg6hPGQ516Z91 zENIP0S5PovnoRA1WpPPdcCQcZ+mpgPt/in74KVJggGHQiklL2F2Dt4Y pLxcTlXD'
    }
    r = self.c.post('/en/ds/foobar.eu.org/', fields)
    self.assertTrue('Illegal record type in zone' in str(r.content))
    self.assertEqual(200, r.status_code)
  def test_domainds_post_ko_4(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))
    fields = {
      # old expired EU.ORG key
      'rr': 'EU.ORG. DNSKEY 256 3 8 AwEAAaD28FYK/09FqyUtDaG0ZnkoB7rarNe5I70Ll1/ZDRlcSCDBffa6 Pr/Za1/MCkTa1DN8ZbGKlk1oXKde081tuRmdmIUpibvLg6hPGQ516Z91 zENIP0S5PovnoRA1WpPPdcCQcZ+mpgPt/in74KVJggGHQiklL2F2Dt4Y pLxcTlXD'
    }
    r = self.c.post('/en/ds/nons.dnssec.tests.eu.org/', fields)
    self.assertTrue('No NS records for domain' in str(r.content))
    self.assertEqual(200, r.status_code)
  def test_domainds_post_ko_5(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))
    fields = {
      # old expired EU.ORG key
      'rr': 'EU.ORG. DNSKEY 256 3 8 AwEAAaD28FYK/09FqyUtDaG0ZnkoB7rarNe5I70Ll1/ZDRlcSCDBffa6 Pr/Za1/MCkTa1DN8ZbGKlk1oXKde081tuRmdmIUpibvLg6hPGQ516Z91 zENIP0S5PovnoRA1WpPPdcCQcZ+mpgPt/in74KVJggGHQiklL2F2Dt4Y pLxcTlXD'
    }
    r = self.c.post('/en/ds/ns.dnssec.tests.eu.org/', fields)
    self.assertTrue('Domain doesn&#39;t match record' in str(r.content) or 'Domain doesn&#x27;t match record' in str(r.content))
    self.assertEqual(200, r.status_code)
  def test_domainds_post_ko_6(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.post('/en/ds/foobar.eu.org/', {})
    self.assertEqual(200, r.status_code)
    self.assertTrue(b'Not eligible to DNSSEC' in r.content)
  def test_domainds_post_ko_7(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))
    r = self.c.post('/en/ds/ns.dnssec.tests.eu.org/', {})
    self.assertEqual(200, r.status_code)
    self.assertTrue(b'Unable to parse line' in r.content)

  def test_special_unlockdom_nx(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'nxdomain.eu.org', 'action': 'lock0', 'submit': 'xxx'
    }
    self.assertRaises(autoreg.dns.db.DomainError, self.c.post, '/en/special/', fields)

  def test_special_unlockdom(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'foobar.eu.org', 'action': 'lock0', 'submit': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Unlocked 1 domain' in str(r.content))

  def test_special_lockdom_nx(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'nxdomain.eu.org', 'action': 'lock1', 'submit': 'xxx'
    }
    self.assertRaises(autoreg.dns.db.DomainError, self.c.post, '/en/special/', fields)

  def test_special_lockdom(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'foobar.eu.org', 'action': 'lock1', 'submit': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Locked 1 domain' in str(r.content))

  def test_special_unholddom_nx(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'nxdomain.eu.org', 'action': 'hold0', 'submit': 'xxx'
    }
    self.assertRaises(autoreg.dns.db.DomainError, self.c.post, '/en/special/', fields)

  def test_special_unholddom(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'foobar.eu.org', 'action': 'hold0', 'submit': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Unheld 1 domain' in str(r.content))
    self.assertFalse(Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_lock)

  def test_special_holddom_nx(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'domains': 'nxdomain.eu.org', 'action': 'hold1', 'submit': 'xxx'
    }
    self.assertRaises(autoreg.dns.db.DomainError, self.c.post, '/en/special/', fields)

  def test_special_hold_unhold_dom(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    self.assertEqual(None, Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_lock)
    self.assertEqual(None, Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_hold)
    fields = {
      'domains': 'foobar.eu.org', 'action': 'hold1', 'submit': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Held 1 domain' in str(r.content))
    self.assertEqual(None, Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_lock)
    self.assertTrue(Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_hold)

    fields['action'] = 'hold0'
    r = self.c.post('/en/special/', fields)
    self.assertEqual(200, r.status_code)
    self.assertTrue('Unheld 1 domain' in str(r.content))
    self.assertEqual(None, Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_lock)
    self.assertFalse(Domains.objects.get(name='FOOBAR', zone_id=self.zone_id).registry_hold)


  def test_special_handle_len(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'handle': 'ZZ1111-FREE',
      'action': 'showdom',
      'submit2': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/domain/list/ZZ1111', r['Location'])

  def test_special_showdom(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    fields = {
      'handle': 'TP1-FREE',
      'action': 'showdom',
      'submit2': 'xxx'
    }
    r = self.c.post('/en/special/', fields)
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/domain/list/TP1', r['Location'])

    fields['handle'] = 'tp1-free'
    r = self.c.post('/en/special/', fields)
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/domain/list/TP1', r['Location'])

  def test_special_400(self):
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    r = self.c.post('/en/special/', {})
    self.assertEqual(400, r.status_code)

  def test_special_403(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.post('/en/special/', {})
    self.assertEqual(403, r.status_code)

  def test_special_get_defer(self):
    """Test correct translation for formatted string"""
    self.assertTrue(self.c.login(username=self.admin_handle, password=self.pw3))
    r = self.c.get('/fr/special/')
    self.assertEqual(200, r.status_code)
    self.assertTrue('Préempter'.encode('UTF-8') in r.content)

  def test_checksoa_ko(self):
    r = self.c.get('/en/soa/AzE.Aze')
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/soa/aze.aze', r['Location'])

  def test_checksoa_1(self):
    r = self.c.get('/en/soa/')
    self.assertEqual(200, r.status_code)
    # need to get all the lines to run the code
    for line in r.streaming_content:
      pass

  def test_checksoa_2(self):
    r = self.c.get('/en/soa/aze.aze')
    self.assertEqual(200, r.status_code)
    # need to get all the lines to run the code
    for line in r.streaming_content:
      pass

  def test_checksoa_3(self):
    r = self.c.get('/en/soa/.')
    self.assertEqual(200, r.status_code)
    # need to get all the lines to run the code
    for line in r.streaming_content:
      pass

  def test_domaindiff_ko_1(self):
    r = self.c.get('/en/domain/diff/h1.history.tests.eu.org')
    self.assertEqual(302, r.status_code)

  def test_domaindiff_ko_2(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.get('/en/domain/diff/h1.history.tests.eu.org')
    self.assertEqual(403, r.status_code)

  def test_domaindiff_ok_1(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))
    r = self.c.get('/en/domain/diff/h1.history.tests.eu.org')
    self.assertEqual(200, r.status_code)
    pre_re = re.compile('<table')
    n = len(pre_re.findall(str(r.content)))
    self.assertEqual(35, n)
    pre_re = re.compile('<tr')
    n = len(pre_re.findall(str(r.content)))
    self.assertEqual(95, n)

  def test_domainhist_ko_1(self):
    r = self.c.get('/en/domain/hist/h1.history.tests.eu.org')
    self.assertEqual(302, r.status_code)

  def test_domainhist_ko_2(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.get('/en/domain/hist/h1.history.tests.eu.org')
    self.assertEqual(403, r.status_code)

  def test_domainhist_ok_1(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))
    r = self.c.get('/en/domain/hist/h1.history.tests.eu.org')
    self.assertEqual(200, r.status_code)
    pre_re = re.compile('<pre class')
    n = len(pre_re.findall(str(r.content)))
    self.assertEqual(35, n)

  def test_domainhistclear_ko_1(self):
    r = self.c.post('/en/domain/histclear/h1.history.tests.eu.org')
    self.assertEqual(302, r.status_code)

  def test_domainhistclear_ko_2(self):
    self.assertTrue(self.c.login(username=self.handle, password=self.pw))
    r = self.c.post('/en/domain/histclear/h1.history.tests.eu.org')
    self.assertEqual(403, r.status_code)

  def test_domainhistclear_ok_1(self):
    self.assertTrue(self.c.login(username='TU1', password=self.pwtu1))

    r = self.c.post('/en/domain/histclear/h1.history.tests.eu.org')
    self.assertEqual(302, r.status_code)
    self.assertEqual('/en/domain/hist/h1.history.tests.eu.org', r['Location'])

    r = self.c.get('/en/domain/hist/h1.history.tests.eu.org')
    self.assertEqual(200, r.status_code)
    pre_re = re.compile('<pre class')
    n = len(pre_re.findall(str(r.content)))
    self.assertEqual(2, n)
