# $Id$

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals


import crypt
import datetime
import io
import random
import re
import socket
import time


import django.contrib.auth
from django.core.exceptions import SuspiciousOperation, PermissionDenied
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import connection
from django import forms
from django.forms.widgets import PasswordInput
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy, ugettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_http_methods


from autoreg.common import domain_delete, fqdn_to_idna
from autoreg.conf import HANDLESUFFIX
import autoreg.dns.db
from autoreg.util import decrypt, encrypt, pwcrypt
from autoreg.whois.db import \
  suffixstrip,suffixadd,Domain,check_handle_domain_auth,handle_domains_dnssec, \
  countries_get
from autoreg.conf import FROMADDR


from ..util import render_to_mail
from ..logs.models import log, Log
from .decorators import blackout, login_active_required, check_handle_fqdn_perms
from .models import Whoisdomains,Contacts,Tokens,DomainContact, check_is_admin
from . import otp
from . import token


RESET_TOKEN_HOURS_TTL = 24
EMAIL_TOKEN_HOURS_TTL = 72
VAL_TOKEN_HOURS_TTL = 72
RESET_TOKEN_TTL = RESET_TOKEN_HOURS_TTL*3600
EMAIL_TOKEN_TTL = EMAIL_TOKEN_HOURS_TTL*3600
VAL_TOKEN_TTL = VAL_TOKEN_HOURS_TTL*3600

domcontact_choices = [('technical', ugettext_lazy('technical')),
                      ('administrative', ugettext_lazy('administrative')),
                      ('zone', ugettext_lazy('zone'))]


#
# Helper functions
#


class Country(object):
  def __iter__(self):
    self.c = countries_get(connection.cursor())
    return self.c.__iter__()


def countries_get_lazy():
  """Lazy evaluation for the country list, so it is not run at module import."""
  return Country


#
# Forms
#

class contactbyemail_form(forms.Form):
  email = forms.EmailField(max_length=100)

class contactbyhandle_form(forms.Form):
  handle = forms.CharField(max_length=20, initial=HANDLESUFFIX,
                           help_text=ugettext_lazy('Your handle'))

class contactbydomain_form(forms.Form):
  domain = forms.CharField(max_length=80, initial='.eu.org',
                           help_text=ugettext_lazy('Domain'))

class contactchange_form(forms.Form):
  pn1 = forms.RegexField(max_length=60, label=ugettext_lazy("Name"),
              regex='^[a-zA-Z \.-]+\s+[a-zA-Z \.-]')
  em1 = forms.EmailField(max_length=64, label=ugettext_lazy("E-mail"))
  ad1 = forms.CharField(max_length=80, label=ugettext_lazy("Organization"))
  ad2 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 1)"))
  ad3 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 2)"),
                        required=False)
  ad4 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 3)"),
                        required=False)
  ad5 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 4)"),
                        required=False)
  ad6 = forms.ChoiceField(initial='', label=ugettext_lazy("Country"),
                          choices=countries_get_lazy())
  ph1 = forms.RegexField(max_length=30, label=ugettext_lazy("Phone"),
                         regex='^\+?[\d\s#\-\(\)\[\]\.]+$', required=False)
  fx1 = forms.RegexField(max_length=30, label=ugettext_lazy("Fax"),
                         regex='^\+?[\d\s#\-\(\)\[\]\.]+$', required=False)
  private = forms.BooleanField(label=ugettext_lazy("Private (not shown in the public Whois)"), required=False)

class contact_form(contactchange_form):
  p1 = forms.CharField(max_length=64, label=ugettext_lazy('Password'),
                       required=False, widget=PasswordInput)
  p2 = forms.CharField(max_length=64, label=ugettext_lazy('Confirm Password'),
                       required=False, widget=PasswordInput)
  policy = forms.BooleanField(label="I accept the Policy", required=True)

class registrant_form(forms.Form):
  # same as contactchange_form minus the email field
  pn1 = forms.RegexField(max_length=60, label=ugettext_lazy("Name"),
              regex='^[a-zA-Z \.-]+\s+[a-zA-Z \.-]')
  # disabled until we get rid of the RIPE model (unshared registrant records)
  #em1 = forms.EmailField(max_length=64, label="E-mail", required=False)
  ad1 = forms.CharField(max_length=80, label=ugettext_lazy("Organization"))
  ad2 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 1)"))
  ad3 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 2)"),
                        required=False)
  ad4 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 3)"),
                        required=False)
  ad5 = forms.CharField(max_length=80, label=ugettext_lazy("Address (line 4)"),
                        required=False)
  ad6 = forms.ChoiceField(initial='', label=ugettext_lazy("Country"),
                          choices=countries_get_lazy())
  ph1 = forms.RegexField(max_length=30, label=ugettext_lazy("Phone"),
                         regex='^\+?[\d\s#\-\(\)\[\]\.]+$', required=False)
  fx1 = forms.RegexField(max_length=30, label=ugettext_lazy("Fax"),
                         regex='^\+?[\d\s#\-\(\)\[\]\.]+$', required=False)
  private = forms.BooleanField(label=ugettext_lazy("Private (not shown in the public Whois)"), required=False)

class domcontact_form(forms.Form):
  contact_type = forms.ChoiceField(choices=domcontact_choices,
                                   label=ugettext_lazy("type"))
  handle = forms.CharField(max_length=20, initial=HANDLESUFFIX)

class contactlogin_form(forms.Form):
  handle = forms.CharField(max_length=20, initial=HANDLESUFFIX,
                           help_text=ugettext_lazy('Your handle'))
  password = forms.CharField(max_length=64,
                             help_text=ugettext_lazy('Your password'),
                             widget=PasswordInput)

class resetpass_form(forms.Form):
  resettoken = forms.CharField(max_length=30,
                               label=ugettext_lazy('Reset Token'))
  pass1 = forms.CharField(max_length=64,
                          label=ugettext_lazy('New Password'),
                          widget=PasswordInput)
  pass2 = forms.CharField(max_length=64,
                          label=ugettext_lazy('Confirm Password'),
                          widget=PasswordInput)

class changemail_form(forms.Form):
  token = forms.CharField(max_length=30)

class chpass_form(forms.Form):
  pass0 = forms.CharField(max_length=64,
                          label=ugettext_lazy('Current Password'),
                          widget=PasswordInput)
  pass1 = forms.CharField(max_length=64,
                          label=ugettext_lazy('New Password'),
                          widget=PasswordInput)
  pass2 = forms.CharField(max_length=64,
                          label=ugettext_lazy('Confirm Password'),
                          widget=PasswordInput)

#
# 'view' functions called from urls.py and friends
#

#
# public pages
#

@require_http_methods(["GET", "POST"])
@blackout
def login(request):
  """Login page"""
  if request.method == "GET":
    next = request.GET.get('next', None)
    if request.user.is_authenticated and request.user.is_active:
      return HttpResponseRedirect(reverse(domainlist))
    f = contactlogin_form()
    form = f.as_table()
    request.session.set_test_cookie()
    vars = { 'form': form, 'next': next }
    return render(request, 'whois/login.html', vars)

  if request.method == "POST":
    next = request.POST.get('next', reverse(domainlist))
    vars = {'next': next}
    if request.user.is_authenticated:
      #django.contrib.auth.logout(request)
      return HttpResponseRedirect(next)
    if not request.session.test_cookie_worked():
      vars['msg'] = _("Please enable cookies")
      vars['form'] = contactlogin_form().as_table()
      return render(request, 'whois/login.html', vars)
    else:
      #request.session.delete_test_cookie()
      pass
    handle = request.POST.get('handle', '').upper()
    password = request.POST.get('password', '')
    handle = suffixstrip(handle)

    vars = {'next': request.path,
            'form': contactlogin_form().as_table()}
    user = django.contrib.auth.authenticate(username=handle, password=password)
    if user is not None:
      c = Contacts.objects.filter(handle=handle)
      if c.count() != 1:
        raise SuspiciousOperation
      v = c[0].validated_on
      if not v:
        vars['msg'] = _("You need to validate your account. " \
                      "Please check your e-mail for the validation link.")
      elif not user.is_active:
        vars['msg'] = _("Sorry, your account has been disabled")
      elif otp.totp_is_active(handle):
        # send the user to a 2nd page for two-factor authentication
        request.session['1fa'] = handle
        return HttpResponseRedirect(reverse('login2fa'))
      else:
        # no OTP to process, login complete
        log(handle, action='login')
        django.contrib.auth.login(request, user)
        return HttpResponseRedirect(next)
    else:
      vars['msg'] = _("Your username and/or password is incorrect")
    return render(request, 'whois/login.html', vars)

@require_http_methods(["GET", "POST"])
@blackout
def contactbydomain(request, fqdn=None):
  if request.method == "GET" and fqdn is None:
    f = contactbydomain_form()
    form = f.as_table()
    vars = { 'form': form }
    return render(request, 'whois/contactdomainform.html', vars)
  elif request.method == "POST" or fqdn is not None:
    fqdn = fqdn or request.POST.get('domain', '')
    handles = DomainContact.objects \
               .filter(whoisdomain_id__fqdn=fqdn.upper(),
                       contact_id__email__isnull=False) \
               .distinct().values_list('contact_id__handle', flat=True)
    vars = { 'handles': handles }
    return render(request, 'whois/contactdomain.html', vars)
  else:
    raise SuspiciousOperation

@require_http_methods(["GET", "POST"])
@blackout
def makeresettoken(request, handle=None):
  """Password reset step 1: send a reset token to the contact email address"""
  if request.method == "GET":
    if handle:
      f = contactbyhandle_form(initial={ 'handle': suffixadd(handle) })
    else:
      f = contactbyhandle_form()
    form = f.as_table()
    vars = { 'form': form }
    return render(request, 'whois/resetpass.html', vars)

  handle = request.POST.get('handle', '').upper()
  fullhandle = handle
  handle = suffixstrip(handle)
  ctl = Contacts.objects.filter(handle=handle)
  if len(ctl) == 0:
    vars = { 'ehandle': suffixadd(handle),
             'next': request.path }
    return render(request, 'whois/contactnotfound.html', vars)
  if len(ctl) != 1:
    raise SuspiciousOperation
  ct = ctl[0]

  # create new token
  token.token_clear(ct.id, action="pwreset")
  mytoken = token.token_set(ct.id, action="pwreset", ttl=RESET_TOKEN_TTL)

  absurl = request.build_absolute_uri(reverse(resetpass2,
                                              args=[handle]))
  if not ct.email:
     vars = { 'msg': _("Sorry, this is an internal handle, it has no associated e-mail.") }
     return render(request, 'whois/msgnext.html', vars)
  if not render_to_mail('whois/resetpass.mail',
                         { 'to': ct.email,
                           'absurl': absurl,
                           'remoteip': request.META.get('REMOTE_ADDR', None),
                           'handle': fullhandle,
                           'token': mytoken }, FROMADDR, [ ct.email ],
                        request):
     vars = { 'msg': _("Sorry, error while sending mail."
                       " Please try again later.") }
     return render(request, 'whois/msgnext.html', vars)
  vars = { 'ehandle': suffixadd(handle) }
  return render(request, 'whois/tokensent.html', vars)

@require_http_methods(["GET", "POST"])
@blackout
def resetpass2(request, handle):
  """Password reset step 2:
     check provided reset token and force indicated password
     on the designated contact."""
  f = resetpass_form()
  form = f.as_table()
  vars = {'form': form}
  if request.method == "GET":
    return render(request, 'whois/resetpass2.html', vars)

  ctl = Contacts.objects.filter(handle=handle)
  if len(ctl) < 1:
    return render(request, 'whois/resetpass2.html', vars)
  ct = ctl[0]
  pass1 = request.POST.get('pass1', 'A')
  pass2 = request.POST.get('pass2', 'B')
  if pass1 != pass2:
    vars['msg'] = _("They don't match, try again")
    return render(request, 'whois/resetpass2.html', vars)
  if len(pass1) < 8:
    vars['msg'] = _("Password should be at least 8 chars")
    return render(request, 'whois/resetpass2.html', vars)
  mytoken = request.POST.get('resettoken', 'C')
  tkl = token.token_find(ct.id, "pwreset")
  if len(tkl) > 1:
    raise SuspiciousOperation
  if len(tkl) == 0 or mytoken != tkl[0].token:
    vars['msg'] = _("Invalid reset token")
    return render(request, 'whois/resetpass2.html', vars)
  tk = tkl[0]
  ct.passwd = pwcrypt(pass1)
  ct.save()
  tk.delete()
  vars = { 'ehandle': suffixadd(handle) }
  return render(request, 'whois/passchanged.html', vars)

@require_http_methods(["GET", "POST"])
@blackout
def contactcreate(request):
  """Contact creation page"""
  if request.user.is_authenticated and request.user.is_active:
    handle = request.user.username
  else:
    handle = None
  vars = {}
  p_errors = []
  if request.method == "GET":
    form = contact_form(initial={'private': True})
  else:
    form = contact_form(request.POST)

    # validate password field by hand
    p1 = request.POST.get('p1', '')
    p2 = request.POST.get('p2', '')
    if p1 != p2:
      p_errors = [_("Passwords don't match")]
    elif len(p1) < 8:
      p_errors = [_("Password too short")]

    if form.is_valid() and not p_errors:
      #
      # Process contact creation
      #
      d = {}
      for i in ['pn', 'em', 'ph', 'fx']:
        v = form.cleaned_data.get(i + '1', None)
        if v != '':
          d[i] = [v]
      ad = []
      for i in ['ad1', 'ad2', 'ad3', 'ad4', 'ad5']:
        a = form.cleaned_data.get(i, None)
        if a is not None and a != '':
          ad.append(a)
      co = form.cleaned_data.get('ad6', None)
      if co is not None and co != '':
        d['co'] = [ co ]
      d['ad'] = ad
      d['ch'] = [(request.META.get('REMOTE_ADDR', 'REMOTE_ADDR_NOT_SET'), None)]
      d['pr'] = [bool(form.cleaned_data['private'])]

      from autoreg.whois.db import Person

      p = Person(connection.cursor(), passwd=encrypt(pwcrypt(p1)),
                 validate=False)
      if p.from_ripe(d):
        p.insert()
        valtoken = token.token_set(p.cid, "contactval", ttl=VAL_TOKEN_TTL)
        ehandle = suffixstrip(p.gethandle())
        absurl = request.build_absolute_uri(reverse(contactvalidate,
                                                    args=[ehandle.upper(),
                                                          valtoken]))
        if not render_to_mail('whois/contactcreate.mail',
                               {'absurl': absurl,
                                'valtoken': valtoken,
                                'whoisdata': p.__str__(),
                                'to': d['em'][0],
                                'handle': suffixadd(ehandle)},
                               FROMADDR, [d['em'][0]], request):
          vars['msg'] = _("Sorry, error while sending mail."
                          " Please try again later.")
          return render(request, 'whois/msgnext.html', vars)
        vars['msg'] = _("Contact successfully created as %(handle)s. Please check instructions sent to %(email)s to validate it.") \
                        % {'handle': suffixadd(ehandle), 'email': d['em'][0]}
        return render(request, 'whois/msgnext.html', vars)
      # else: fall through
  vars.update({'form': form,
               'p_errors': p_errors})
  return render(request, 'whois/contactcreate.html', vars)

@require_http_methods(["GET", "POST"])
@blackout
def contactvalidate(request, handle, valtoken):
  """Contact validation page"""
  if request.user.is_authenticated:
    django.contrib.auth.logout(request)

  # XXX: strange bug causes a SIGSEGV if we use valtoken from URL parsing
  # after a POST; instead we pass it as an hidden FORM variable,
  # hence the following two lines.
  if request.method == "POST":
    valtoken = request.POST.get('valtoken')

  msg = None
  ctl = Contacts.objects.filter(handle=handle)
  if len(ctl) != 1:
    msg = _("Sorry, contact handle or validation token is not valid.")
  else:
    tkl = token.token_find(ctl[0].id, "contactval")
    if len(tkl) != 1 or tkl[0].token != valtoken:
      msg = _("Sorry, contact handle or validation token is not valid.")
  if msg:
    vars = { 'msg': msg }
    return render(request, 'whois/msgnext.html', vars)
  ct = ctl[0]
  if request.method == "GET":
    vars = { 'handle': suffixadd(handle), 'email': ct.email,
             'valtoken': valtoken }
    return render(request, 'whois/contactvalidate.html', vars)
  elif request.method == "POST":
    ct.validated_on = timezone.now()
    ct.save()
    tkl[0].delete()
    vars = { 'msg': _("Your contact handle is now valid.") }
    return render(request, 'whois/msgnext.html', vars)

@require_http_methods(["GET"])
@blackout
def domain(request, fqdn):
  """Whois from domain FQDN"""
  f = fqdn.upper()
  try:
    dom = Whoisdomains.objects.get(fqdn=f)
  except Whoisdomains.DoesNotExist:
    dom = None
  if dom is None:
    vars = { 'fqdn': fqdn }
    return render(request, 'whois/domainnotfound.html', vars)
  cl = dom.domaincontact_set.all()
  vars = { 'whoisdomain': dom, 'domaincontact_list': cl }
  return render(request, 'whois/fqdn.html', vars)

# private pages

@require_http_methods(["GET", "POST"])
@login_active_required
@cache_control(private=True)
@blackout
def chpass(request):
  """Contact password change"""
  handle = request.user.username
  f = chpass_form()
  form = f.as_table()
  vars = {'form': form}
  if request.method == "GET":
    return render(request, 'whois/chpass.html', vars)

  pass0 = request.POST.get('pass0', '')
  pass1 = request.POST.get('pass1', '')
  pass2 = request.POST.get('pass2', '')
  if pass1 != pass2:
    vars['msg'] = _("They don't match, try again")
    return render(request, 'whois/chpass.html', vars)
  if len(pass1) < 8:
    vars['msg'] = _("Password should be at least 8 chars")
    return render(request, 'whois/chpass.html', vars)

  ctlist = Contacts.objects.filter(handle=handle)
  if len(ctlist) != 1:
    raise SuspiciousOperation

  ct = ctlist[0]
  passwd = ct.passwd
  if len(passwd) > 123:
    passwd = decrypt(passwd)
  if passwd != crypt.crypt(pass0, passwd):
    vars['msg'] = _("Current password is not correct")
    return render(request, 'whois/chpass.html', vars)
  ct.passwd = encrypt(pwcrypt(pass1))
  ct.save()
  del vars['form']
  vars['ehandle'] = suffixadd(handle)
  return render(request, 'whois/passchanged.html', vars)

@require_http_methods(["GET"])
@login_active_required
@cache_control(private=True, max_age=10)
@blackout
def domainlist(request, handle=None):
  """Display domain list for a contact"""
  if handle is not None:
    if not check_is_admin(request.user.username):
      raise PermissionDenied
  else:
    handle = request.user.username

  domds = handle_domains_dnssec(connection.cursor(), handle)

  domds = [(d[0].lower(), d[1], d[2], d[3], d[4], d[5], d[6], d[7],
            fqdn_to_idna(d[0])) for d in domds]
  domds.sort(key=lambda d: d[8])

  paginator = Paginator(domds, 50)

  page = request.GET.get('page')
  try:
    dompage = paginator.page(page)
  except PageNotAnInteger:
    dompage = paginator.page(1)
  except EmptyPage:
    dompage = paginator.page(paginator.num_pages)

  vars = { 'list': dompage,
           'handle': handle }
  return render(request, 'whois/domainlist.html', vars)

@require_http_methods(["GET", "POST"])
@login_active_required
@check_handle_fqdn_perms
@cache_control(private=True)
@blackout
def contactchange(request, fqdn=None):
  """Contact or registrant modification page.
     If registrant, fqdn contains the associated domain FQDN.
  """
  if fqdn and fqdn != fqdn.lower():
    return HttpResponseRedirect(reverse(contactchange,
                                        args=[fqdn.lower()]))
  handle = request.user.username

  if fqdn:
    dom = Whoisdomains.objects.get(fqdn=fqdn.upper())
    cl = dom.domaincontact_set.filter(contact_type__name='registrant')
    if len(cl) != 1:
      raise SuspiciousOperation
    ehandle = cl[0].contact.handle
  else:
    ehandle = handle

  vars = {}
  if request.method == "GET":
    c = Contacts.objects.get(handle=ehandle)
    initial = c.initial_form()
    if fqdn:
      vars['fqdn'] = fqdn
      idna = fqdn_to_idna(fqdn)
      vars['idna'] = idna
      vars['form'] = registrant_form(initial=initial)
    else:
      vars['ehandle'] = suffixadd(ehandle)
      vars['form'] = contactchange_form(initial=initial)
    return render(request, 'whois/contactchange.html', vars)

  if fqdn:
    form = registrant_form(request.POST)
  else:
    form = contactchange_form(request.POST)
  if form.is_valid():
    c = Contacts.objects.get(handle=ehandle)
    ad = []
    for i in '12345':
      k = 'ad%c' % i
      if form.cleaned_data[k] != '':
        ad.append(form.cleaned_data[k])
    changed = False
    emailchanged = False
    if c.name != form.cleaned_data['pn1']:
      c.name = form.cleaned_data['pn1']
      changed = True
    if ('em1' in form.cleaned_data
        and form.cleaned_data['em1'] != ''
        and c.email != form.cleaned_data['em1']):
      newemail = form.cleaned_data['em1']
      emailchanged = True
    for i in ['fx1', 'ph1']:
      if form.cleaned_data[i] == '':
        form.cleaned_data[i] = None
    if c.phone != form.cleaned_data['ph1']:
      c.phone = form.cleaned_data['ph1']
      changed = True
    if c.fax != form.cleaned_data['fx1']:
      c.fax = form.cleaned_data['fx1']
      changed = True
    if c.country != form.cleaned_data['ad6']:
      c.country = form.cleaned_data['ad6']
      changed = True
    if c.addr != '\n'.join(ad):
      c.addr = '\n'.join(ad)
      changed = True
    if c.private != form.cleaned_data['private']:
      c.private = form.cleaned_data['private']
      changed = True
    if changed:
      c.updated_on = None	# set to NOW() by the database
      c.updated_by = suffixadd(request.user.username)
      c.save()
    if emailchanged:
      token.token_clear(c.id, "changemail")
      mytoken = token.token_set(c.id, "changemail", newemail, EMAIL_TOKEN_TTL)
      absurl = request.build_absolute_uri(reverse(changemail))
      if not render_to_mail('whois/changemail.mail',
                             {'to': newemail,
                              'absurl': absurl,
                              'handle': suffixadd(ehandle),
                              'newemail': newemail,
                              'token': mytoken }, FROMADDR, [ newemail ],
                             request):
        vars = { 'msg': _("Sorry, error while sending mail."
                          " Please try again later.") }
        return render(request, 'whois/msgnext.html', vars)
      return HttpResponseRedirect(reverse(changemail))
    if fqdn:
      return HttpResponseRedirect(reverse(domainedit,
                                          args=[fqdn]))
    else:
      vars['msg'] = _("Contact information changed successfully")
      return render(request, 'whois/msgnext.html', vars)
  else:
    vars['form'] = form
    return render(request, 'whois/contactchange.html', vars)

@require_http_methods(["GET", "POST"])
@login_active_required
@cache_control(private=True)
@blackout
def changemail(request):
  """Email change step 2:
     check provided change email token and force indicated email
     on the designated contact."""
  handle = request.user.username
  f = changemail_form()
  form = f.as_table()
  vars = {'form': form}

  ctl = Contacts.objects.filter(handle=handle)
  if len(ctl) != 1:
    raise SuspiciousOperation
  ct = ctl[0]
  tkl = token.token_find(ct.id, "changemail")
  if len(tkl) > 1:
    raise SuspiciousOperation
  if len(tkl) == 0:
      vars['msg'] = _("Sorry, didn't find any waiting email address change.")
      return render(request, 'whois/changemail.html', vars)
  tk = tkl[0]

  vars['newemail'] = tk.args

  if request.method == "GET":
    return render(request, 'whois/changemail.html', vars)

  mytoken = request.POST.get('token', 'C')
  if mytoken != tk.token:
    vars['msg'] = _("Invalid token")
    return render(request, 'whois/changemail.html', vars)
  newemail = tk.args
  ct.email = newemail
  ct.save()
  tk.delete()
  return render(request, 'whois/emailchanged.html', vars)

@require_http_methods(["POST"])
@login_active_required
@cache_control(private=True)
@blackout
def domaineditconfirm(request, fqdn):
  """Request confirmation for self-deletion of a contact"""
  nexturi = reverse(domainedit, args=[fqdn])
  vars = {'fqdn': fqdn, 'posturi': nexturi}
  contact_type = request.POST.get('contact_type', None)
  handle = request.POST.get('handle', None)
  if contact_type and handle:
    vars['contact_type'] = contact_type
    return render(request, 'whois/domaineditconfirm.html', vars)
  return HttpResponseRedirect(nexturi)

@require_http_methods(["GET", "POST"])
@login_active_required
@check_handle_fqdn_perms
@cache_control(private=True, max_age=10)
@blackout
def domainedit(request, fqdn):
  """Edit domain contacts"""
  # list of shown and editable contact types
  typelist = ["administrative", "technical", "zone"]

  handle = request.user.username

  if fqdn != fqdn.lower():
    return HttpResponseRedirect(reverse(domainedit, args=[fqdn.lower()]))

  f = fqdn.upper()
  try:
    dom = Whoisdomains.objects.get(fqdn=f)
  except Whoisdomains.DoesNotExist:
    dom = None
  if dom is None:
    vars = { 'fqdn': fqdn, 'idna': fqdn_to_idna(fqdn) }
    return render(request, 'whois/domainnotfound.html', vars)

  domds = handle_domains_dnssec(connection.cursor(), None, fqdn)
  if len(domds) != 1:
    raise SuspiciousOperation
  has_ns, has_ds, can_ds = domds[0][1], domds[0][7], domds[0][2]
  registry_hold, end_grace_period = domds[0][5], domds[0][6]

  dbdom = Domain(connection.cursor(), did=dom.id)
  dbdom.fetch()

  msg = None

  if request.method == "POST":
    if 'submitd' in request.POST or 'submita' in request.POST:
      contact_type = request.POST['contact_type']
      chandle = suffixstrip(request.POST['handle'].upper())
      ctl = Contacts.objects.filter(handle=chandle)
      if len(ctl) == 0:
        msg = _("Contact %s not found") % suffixadd(chandle)
      elif len(ctl) != 1:
        raise SuspiciousOperation
      else:
        cid = ctl[0].id
        if contact_type[0] not in 'atz':
          raise SuspiciousOperation
        code = contact_type[0] + 'c'
        if 'submitd' in request.POST:
          if cid in dbdom.d[code]:
            numcontacts = 0
            for i in 'atz':
              numcontacts += len(dbdom.d[i+'c'])
            if numcontacts == 1:
              # Refuse deletion of the last contact
              msg = _("Sorry, must leave at least one contact!")
            else:
              log(handle, action='contactdel',
                  message=fqdn + ' ' + suffixadd(chandle))
              dbdom.d[code].remove(cid)
              dbdom.update()
          else:
            msg = _("%s is not a contact") % suffixadd(chandle)
          # Fall through to updated form display
        elif 'submita' in request.POST:
          if ctl[0].validated_on is None:
            msg = _("%(handle)s must be validated first") \
                  % {'handle': suffixadd(chandle)}
          elif cid not in dbdom.d[code]:
            log(handle, action='contactadd',
                message=fqdn + ' ' + suffixadd(chandle))
            dbdom.d[code].append(cid)
            dbdom.update()
          else:
            msg = _("%(handle)s is already a %(contact_type)s contact") \
                  % {'handle': suffixadd(chandle),
                     'contact_type': _(contact_type)}
          # Fall through to updated form display
    else:
      raise SuspiciousOperation

  # handle GET or end of POST

  # get contact list
  cl = dom.domaincontact_set.order_by('contact_type', 'contact__handle')
  formlist = []
  for c in cl:
    ct = c.contact_type.name
    if ct in typelist:
      cthandle = c.contact.handle
      if cthandle == handle:
        posturi = reverse(domaineditconfirm, args=[f.lower()])
      else:
        posturi = ''
      formlist.append({'contact_type': ugettext_lazy(ct),
                       'handle': suffixadd(cthandle),
                       'posturi': posturi })

  idna = fqdn_to_idna(fqdn)

  vars = {'whoisdomain': dom, 'domaincontact_list': cl,
          'fqdn': fqdn, 'idna': idna,
          'msg': msg,
          'formlist': formlist,
          'whoisdisplay': str(dbdom),
          'has_ns': has_ns, 'has_ds': has_ds, 'can_ds': can_ds,
          'registry_hold': registry_hold, 'end_grace_period': end_grace_period,
          'addform': { 'domcontact_form': domcontact_form()} }
  return render(request, 'whois/domainedit.html', vars)

@require_http_methods(["POST"])
@login_active_required
@check_handle_fqdn_perms
@cache_control(private=True)
@blackout
def domaindelete(request, fqdn):
  if fqdn != fqdn.lower():
    return HttpResponseRedirect(reverse(domaindelete, args=[fqdn.lower()]))

  dd = autoreg.dns.db.db(dbc=connection.cursor())
  dd.login('autoreg')

  out = io.StringIO()

  err, ok = None, False
  try:
    ok = domain_delete(dd, fqdn, out, None)
  except autoreg.dns.db.AccessError as e:
    err = str(e)
  except autoreg.dns.db.DomainError as e:
    err = str(e)

  if not ok or err:
    if err:
      msg = err;
    else:
      msg = _('Sorry, domain deletion failed, please try again later.')
    vars = {'msg': msg}
    return render(request, 'whois/msgnext.html', vars)

  return HttpResponseRedirect(reverse(domainedit, args=[fqdn.lower()]))

@require_http_methods(["POST"])
@login_active_required
@check_handle_fqdn_perms
@cache_control(private=True)
@blackout
def domainundelete(request, fqdn):
  if fqdn != fqdn.lower():
    raise PermissionDenied

  dd = autoreg.dns.db.db(dbc=connection.cursor())
  dd.login('autoreg')

  dd.undelete(fqdn, None)

  return HttpResponseRedirect(reverse(domainedit, args=[fqdn]))

@require_http_methods(["POST"])
@login_active_required
def logout(request):
  """Logout page"""
  django.contrib.auth.logout(request)
  return HttpResponseRedirect(reverse(login))
