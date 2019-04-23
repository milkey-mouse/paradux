#!/usr/bin/python
#
# Collects the settings for this instance of paradux
#
# Copyright (C) 2019 and later, Paradux project.
# All rights reserved. License: see package.
#

import os
import os.path
import paradux.configuration.datasets
import paradux.configuration.secrets
import paradux.configuration.stewards
import paradux.configuration.user
import paradux.logging
import paradux.utils
import pathlib
import posixpath
import random
import re
import shutil
import threading


# Default paradux data directory
DEFAULT_DIRECTORY = posixpath.join(pathlib.Path.home(), '.paradux')


def create(args):
    """
    Create a Settings objects from paradux defaults, overridden by command-
    line arguments if they are given.
    Note: this does not touch any of the private/encrypted info.

    args: parsed command-line arguments
    return: Settings object
    """
    if hasattr(args,'image_size'):
        return Settings(args.directory, args.image_size)
    else:
        return Settings(args.directory, None)


class Settings:
    """
    Collects information about the paradux settings in effect during this run

    directory: the paradux data directory
    """
    def __init__(self, directory, image_size) :
        self.directory         = directory
        self.image_size        = image_size

        self.recovery_key_size = 32 # LUKS parameter
        self.recovery_key_slot =  7 # LUKS parameter
        self.everyday_key_slot =  0 # LUKS parameter

        self.image_file        = self.directory + '/configuration.img'        # LUKS file
        self.crypt_device_name = 'paradux'                                    # short name of the device created by cryptsetup
        self.crypt_device_path = '/dev/mapper/' + self.crypt_device_name      # path name of the device created by cryptsetup
        self.image_mount_point = self.directory + '/configuration'            # mount point for the image

        self.datasets_config_file      = self.image_mount_point + '/datasets.json'      # configuration JSON for datasets
        self.temp_datasets_config_file = self.image_mount_point + '/datasets.temp.json' # being edited configuration JSON for datasets
        self.stewards_config_file      = self.image_mount_point + '/stewards.json'      # configuration JSON for stewards
        self.temp_stewards_config_file = self.image_mount_point + '/stewards.temp.json' # being edited configuration JSON for stewards
        self.user_config_file          = self.image_mount_point + '/user.json'          # configuration JSON for user info
        self.temp_user_config_file     = self.image_mount_point + '/user.tmp.json'      # being edited configuration JSON for user info
        self.secrets_config_file       = self.image_mount_point + '/secrets.json'       # configuration JSON for secrets

        self.key_fifo = '/tmp/' + str(random.SystemRandom().randint(0, 1<<32)) # used to pipe a secret into cryptsetup

        self.datasetsConfiguration = None # allocated as needed
        self.stewardsConfiguration = None # allocated as needed
        self.userConfiguration     = None # allocated as needed
        self.secretsConfiguration  = None # allocated as needed


    def createAndMountImage(self, recoverySecret):
        """
        Create the initial LUKS image and mounts it. This does not initialize anything
        inside the image.

        recoverySecret: the recovery secret
        return: void
        throws: exception if the image exists already
        """

        paradux.logging.info('Creating image:', self.image_file)

        if self._image_exists():
            raise FileExistsError(self.image_file)

        if self._cryptsetup_isopen():
            raise FileExistsError(self.crypt_device_path)
            
        self._image_create(recoverySecret)
        self._cryptsetup_open()
        self._image_format()
        self._image_mount()
        self._image_set_permissions()


    def mountImage(self):
        """
        Mount the LUKS image at the designated mount point

        return: void
        """

        paradux.logging.info('Mounting image:', self.image_file)

        if not self._image_exists():
            raise FileNotFoundError(self.image_file)

        self._cryptsetup_open()
        self._image_mount()


    def populateWithInitialData(self, min_stewards, nbits, recoverySecret):
        """
        Initialize this configuration by creating default files inside the image.        

        min_stewards: the number of stewards required to recover
        nbit: length of the recovery secret
        recoverySecret: the secret for recovery
        return: void
        """

        paradux.logging.info('Populating with initial default data')

        paradux.configuration.datasets.saveInitial(self.datasets_config_file)
        paradux.configuration.stewards.saveInitial(self.stewards_config_file)
        paradux.configuration.user.saveInitial(self.user_config_file)
        paradux.configuration.secrets.createAndSaveInitial(nbits, recoverySecret, min_stewards, self.secrets_config_file)


    def getDatasetsConfiguration(self):
        """
        Obtain the current configuration of the datasets.

        return: DatasetsConfiguration
        """
        if self.datasetsConfiguration == None:
            self.datasetsConfiguration = paradux.configuration.datasets.createFromFile( self.datasets_config_file, self.temp_datasets_config_file )
        return self.datasetsConfiguration


    def getStewardsConfiguration(self):
        """
        Obtain the current configuration of the stewards.

        return: StewardsConfiguration
        """
        if self.stewardsConfiguration == None:
            self.stewardsConfiguration = paradux.configuration.stewards.createFromFile( self.stewards_config_file, self.temp_stewards_config_file )
        return self.stewardsConfiguration


    def getUserConfiguration(self):
        """
        Obtain the current configuration of the paradux user.

        return: UserConfiguration
        """
        if self.userConfiguration == None:
            self.userConfiguration = paradux.configuration.user.createFromFile( self.user_config_file, self.temp_user_config_file )
        return self.userConfiguration


    def getSecretsConfiguration(self):
        """
        Obtain the current configuration of the secrets.

        return: SecretsConfiguration
        """
        if self.secretsConfiguration == None:
            self.secretsConfiguration = paradux.configuration.secrets.createFromFile( self.secrets_config_file )
        return self.secretsConfiguration


    def getStewardPackages(self):
        """
        Obtain a list of StewardPackage ready for export.

        return: list
        """
        # ret = map( lambda sp : StewardPackage(, self.stewardInfos )
        # return ret


    def hasEverydayPassphrase(self, imageFile=None):
        """
        Has the image the everyday passphrase set

        imageFile: name of the image file, or defaults to self.image_file
        """
        if imageFile is None:
            imageFile = self.image_file

        usedSlots = self._cryptsetup_used_keyslots(imageFile)
        return self.everyday_key_slot in usedSlots


    def hasRecoverySecret(self, imageFile=None):
        """
        Has the image the recovery secret set

        imageFile: name of the image file, or defaults to self.image_file
        """
        if imageFile is None:
            imageFile = self.image_file

        usedSlots = self._cryptsetup_used_keyslots(imageFile)
        return self.recovery_key_slot in usedSlots


    def exportAll(self,exportFile):
        """
        Export the image to the named file, stripping the everyday passphrase.

        exportFile: the file to export to
        """
        if os.path.isfile(exportFile):
            raise FileExistsError(exportFile)

        if not self.hasRecoverySecret():
            paradux.logging.fatal('No recovery secret has been set. Cannot export.')

        shutil.copyfile(self.image_file, exportFile)
        os.chmod(exportFile, 0o600)

        if paradux.utils.myexec(
                  'cryptsetup luksKillSlot'
                + ' --batch-mode'
                + " '" + exportFile + "'"
                + " " + str(self.everyday_key_slot)):
            paradux.logging.fatal('cryptsetup luksKillSlot failed')

        if self.hasEverydayPassphrase(exportFile):
            paradux.logging.fatal('Export failed: everyday passphrase has not been removed.')

        if not self.hasRecoverySecret(exportFile):
            paradux.logging.fatal('Export failed: no recovery secret has been set.')

        paradux.logging.info( 'Exported file without everyday passphrase:', exportFile )

        
    def cleanup(self):
        """
        Do whatever necessary to clean up and make private data
        inaccessible again.

        return: void
        """

        paradux.logging.info('Cleaning up')

        if self._image_ismounted():
            self._image_umount()

        if self._cryptsetup_isopen():
            self._cryptsetup_close()


    def _image_exists(self):
        """
        Determine whether the LUKS image exists

        return: True or False
        """
        paradux.logging.trace('file exists?', self.image_file)
        return os.path.isfile(self.image_file)


    def _image_create(self, recoverySecret):
        """
        Create the image file

        recoverySecret: the recovery secret
        return: void
        """
        image_dir = os.path.dirname(self.image_file)
        if not os.path.isdir(image_dir):
            paradux.logging.trace('creating path to', image_dir)
            os.makedirs(image_dir)

        with open(self.image_file, "w") as file:
            paradux.logging.trace('creating file', self.image_file, 'of size', self.image_size)
            file.truncate(self.image_size)

        recoveryPassphrase = _secretToPassphrase(recoverySecret)

        if paradux.utils.myexec(
                  'cryptsetup luksFormat'
                + ' --batch-mode'
                + ' --key-slot=' + str(self.recovery_key_slot)
                + ' --key-file=-' # read from stdin
                + " '" + self.image_file + "'",
                recoveryPassphrase ):
            paradux.logging.fatal('cryptsetup luksFormat failed')

        # Adding a second key requires that the previous key is provided.
        # Reading both from stdin does not seem to work.
        # We don't want to save either of those to disk.
        # So we create a named pipe, and pipe to it on a different thread, yay!

        os.mkfifo(self.key_fifo, 0o600)

        class Piper(threading.Thread):
            def __init__(self,fileName, content):
                super().__init__()
                self.fileName = fileName
                self.content  = content

            def run(self):
                with open(self.fileName, "wb") as file:
                    file.write(self.content)
                

        piper = Piper(self.key_fifo, recoveryPassphrase)
        piper.start()


        print("""
Please set your everyday passphrase for paradux.
Make sure this passphrase is long, hard to guess, and DO NOT write it down anywhere.
If you lose it, paradux lets you recover with the help of your stewards, once you
have set those up.
""")

        if paradux.utils.myexec(
                  'cryptsetup luksAddKey'
                + ' --batch-mode'
                + ' --key-slot=' + str(self.everyday_key_slot)
                + ' --key-file=' + self.key_fifo
                + " '" + self.image_file + "'" ):
            paradux.logging.fatal('cryptsetup luksAddKey failed')

        piper.join()
        os.remove(self.key_fifo)


    def _image_format(self):
        """
        Format the image by means of the cryptsetup device

        return: void
        """
        if paradux.utils.myexec("sudo mkfs.ext4 '" + self.crypt_device_path + "'"):
            paradux.logging.fatal('making ext4 filesystem failed')


    def _image_mount(self):
        """
        Mount the LUKS image

        return: void
        """
        paradux.logging.trace('mount path exists?', self.image_mount_point)
        if not os.path.isdir(self.image_mount_point):
            paradux.logging.trace('creating path to mount point', self.image_mount_point)
            os.makedirs(self.image_mount_point, mode=0o700)

        if paradux.utils.myexec("sudo mount '" + self.crypt_device_path + "' '" + self.image_mount_point + "'"):
            paradux.logging.fatal('mount failed')


    def _image_ismounted(self):
        """
        Determine whether the LUKS image is mounted

        return: True or False
        """
        paradux.logging.trace('is mounted?', self.image_mount_point)
        p = pathlib.Path(self.image_mount_point)
        return p != None and p.is_mount()


    def _image_umount(self):
        """
        Unmount the LUKE image.

        return: void
        """
        if paradux.paradux.utils.myexec("sudo umount '" + self.crypt_device_path + "'"):
            paradux.logging.fatal('umount failed')
    

    def _image_set_permissions(self):
        """
        Set the correct permissions on a new mounted image.

        return: void
        """
        paradux.logging.trace('changing permissions')

        # must be performed as root
        if paradux.paradux.utils.myexec("sudo chown " + str(os.getuid()) + ":" + str(os.getgid()) + " '" + self.image_mount_point + "'"):
            paradux.logging.fatal('chown failed')

        if paradux.paradux.utils.myexec("sudo chmod 0700 '" + self.image_mount_point + "'"):
            paradux.logging.fatal('chmod failed')
        
        paradux.logging.debugAndSuspend( 'Check permissions' )


    def _cryptsetup_open(self):
        """
        Create/open the cryptsetup device
        """
        print("""
Enter your everyday passphrase.
""")
        if paradux.utils.myexec("sudo cryptsetup open '" + self.image_file + "' '" + self.crypt_device_name + "'"):
            paradux.logging.fatal('cryptsetup open failed')
        

    def _cryptsetup_isopen(self):
        """
        Determine whether the cryptsetup device is open

        return: True or False
        """
        # FIXME? Should this use "cryptsetup status <device>", swallout output, and look at exit code?
        paradux.logging.trace('is block device?', self.crypt_device_path)
        p = pathlib.Path(self.crypt_device_path)
        return p != None and p.is_block_device()
        

    def _cryptsetup_close(self):
        """
        Close the cryptsetup device
        """
        if paradux.utils.myexec("sudo cryptsetup close '" + self.crypt_device_name + "'"):
            paradux.logging.fatal('cryptsetup close failed')


    def _cryptsetup_used_keyslots(self, imageFile):
        """
        Determine which LUKS key slots are currently in use.

        imageFile: name of the image file to test
        return: dict of key slot number to True
        """
        (status,out,err) = paradux.utils.myexec("cryptsetup luksDump '" + imageFile + "'", None, True )
        if status:
            paradux.logging.fatal('cryptsetup luksDump failed')

        inKeyslots = False
        ret        = {}
        for line in out.decode('utf8').splitlines():
            if re.match(r'^\S+', line):
                inKeyslots = line.startswith('Keyslots:')
            elif inKeyslots:
                m = re.match(r'^\s*(\d+):', line)
                if m:
                    ret[int(m.group(1))] = True
        return ret


def _secretToPassphrase(secret):
    """
    Convert an integer (used as secret for Shamir) to a passphrase for
    cryptsetup. Use only 7bit ASCII. Cryptsetup supports up to 512 chars.
    Just to be extra safe, we use the range from 32 (space, inclusive)
    through 127 (DEL, exclusive).

    Note: if you change this algorithm, you will break everybody's
    recovery!

    secret: the integer secret
    return: passphrase, as a bytes-like object
    """
    minC =  32
    maxC = 127
    dC   = maxC - minC

    ret = ''
    for i in range(512):
        if secret == 0:
            break
        c = ( secret % dC ) + minC
        ret += chr(c)
        secret = secret // dC
    return bytes(ret, encoding="utf-8")

