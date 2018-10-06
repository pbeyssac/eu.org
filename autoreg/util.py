from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals


import crypt
import random


import six


# parameters for SHA512 hashed passwords
CRYPT_SALT_LEN=16
CRYPT_ALGO='$6$'


def pwcrypt(passwd):
  """Compute a crypt(3) hash suitable for user authentication"""
  # Make a salt
  salt_chars = '0123456789abcdefghijklmnopqstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ/.'
  t = ''.join(random.SystemRandom().choice(salt_chars) \
              for i in range(CRYPT_SALT_LEN))
  if six.PY2:
    passwd = passwd.encode('UTF-8')
  return crypt.crypt(passwd, CRYPT_ALGO + t + '$')
