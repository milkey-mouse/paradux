#!/usr/bin/python
#
# Collects the settings for this instance of paradux
#
# Copyright (C) 2019 and later, Paradux project.
# All rights reserved. License: see package.
#

class Credentials:
    """
    Abstract superclass for all types of username/password and the like
    """
    pass


class PasswordCredentials(Credentials):
    """
    A username/password combination
    """
    def __init__(self, username, usersecret):
        """
        Constructor.

        username: user name
        usersecret: password that goes with the user name
        """
        self.username   = username
        self.usersecret = usersecret


class SshCredentials(Credentials):
    """
    A username/private key pair combination
    """
    def __init__(self, username, private_key):
        """
        Constructor.

        username: user name
        private_key: private SSH key that goes with the user name
        """
        self.username    = username
        self.private_key = private_key
        

class AwsApiCredentials(Credentials):
    """
    A pair of API key and secret access key to access Amazon Web Services via
    its API.
    """
    def __init__(self, apiKey, secretApiKey):
        """
        Constructor.
        
        apiKey: the API key
        secretAccessKey: the secret access key for the API
        """
        self.apiKey       = apiKey
        self.secretApiKey = secretApiKey

