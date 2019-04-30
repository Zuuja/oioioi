from __future__ import unicode_literals

from django.contrib.auth.models import User
from django.db import models


class UserGroup(models.Model):
    """ Group of user which can be moved around contests by teachers """
    owners = models.ManyToManyField(User, related_name='group_owners')
    users = models.ManyToManyField(User)
