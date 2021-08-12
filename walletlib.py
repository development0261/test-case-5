#!/usr/bin/env python3

'''
A Python wrapper for the Shakti walletlib.

History:

* 0.75 Beta (2021-03-13): added the COMPLETE constant and the ability to ask
  the Request class to wait until a request is complete. Also a few tweaks to
  the Session class code.

* 0.74 Beta (2021-03-11): added the submit_poe_feats() and submit_admin_info()
  functions.

* 0.73 Beta (2020-11-20): added the wallet_library_version() function.

* 0.72 Beta (2020-10-17): Another minor fix for the new fallback code.

* 0.71 Beta (2020-10-10): Fixed minor bug in new fallback code.

* 0.7 Beta (2020-10-09): Added fallback code for locating the compiled library,
  as ctypes.util.find_library seems to fail on many machines.

* 0.61 Beta (2020-08-26): fixed a newly-discovered bug in the transfer call.

* 0.6 Beta (2020-08-13): added new setLoggingFileDescriptor function, added new
  transfer-status code.

* 0.5 Beta (2020-08-09): fixed a bug in the transfer function.

* 0.4 Beta (2020-07-31): added access to the getFoundationBytes function, for
  testing purposes.

* 0.3 Beta (2020-07-15): fixed problem with calculate_transfer_fee, added
  separate_transfer_and_fee function. Also renamed module to use underscore
  instead of dash, so it's clear what name to use to import it into another
  script.

* 0.2 Beta (2020-03-24): added access to the new getBlockByTime function, some
  additional addresses to check in the test code, and an explicit return value
  when the test code catches an exception.

* 0.1 Beta (2020-03-06): initial release.
'''

import time
from datetime import datetime
import os.path
import sys
import ctypes, ctypes.util
import platform

VERSION="0.75 Beta"

# To install the library under Linux, use these commands...
#
#   sudo cp libwalletlib.so /usr/lib && sudo ldconfig -nv /usr/lib
#
# ...or have it in the same directory as this Python file or in a directory
# that's listed in the LD_LIBRARY_PATH.
#
# On Windows, the library file needs to be in the current directory or
# somewhere on the path.
#
# On Mac OSX, put it in the same directory as this Python file, or put it in a
# directory that's on the DYLD_FALLBACK_LIBRARY_PATH or DYLD_LIBRARY_PATH.
#
# Notes for other OSes will be added as they're determined.
# _libpath = ctypes.util.find_library('D:\\Logix\\ShaktiCoin\\scale\\walletlib.dll')
_libpath = ctypes.util.find_library('\\libwalletlib.so')
# _libpath = ctypes.util.find_library('\\walletlib.dll')
if _libpath is None:
    # Get the directory of this file. We'll look there for the file, under all
    # OSes, as well as in the OS-specific place(s).
    CWD = os.path.dirname(os.path.realpath(__file__))

    # If the find_library call works, great! If not (and it often doesn't, in
    # my experience), we'll fall back to a manual search.
    system = platform.system()
    if system == "Linux" or system.startswith('CYGWIN_NT'):
        # Under Linux, we'll check the LD_LIBRARY_PATH.
        libnames = [ 'libwalletlib.so' ]
        p = os.getenv('LD_LIBRARY_PATH')
        if p is not None:
            libdirs = p.split(':')
            libdirs.append(CWD)
        else:
            libdirs = [ '/usr/local/lib', '/usr/lib', CWD ]
    elif system == "Windows":
        # Search the PATH for this file.
        libnames = [ 'walletlib.dll' ]
        p = os.getenv('PATH')
        if p is not None:
            libdirs = p.split(';')
            libdirs.append(CWD)
        else:
            libdirs = [ CWD ]
    elif system == "Darwin":
        # The MacOSX kernel. This developer doesn't have a MacOS machine
        # available to test, the following is based on information found on
        # Stack Overflow and may not be correct.
        libnames = [ 'libwalletlib.dylib', 'libwalletlib.so' ]
        libdirs = [ ]
        for e in ('DYLD_FALLBACK_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
            p = os.getenv(e)
            if p is not None:
                libdirs += p.split(':')
        if len(libdirs) == 0:
            libdirs += [ '/usr/local/lib', '/usr/lib', CWD ]
    else:
        raise Exception("cannot locate walletlib (and OS is unrecognized)")

    # We have 'libnames' and 'libdirs'.
    class Found(Exception): pass
    try:
        for pname in libnames:
            for pdir in libdirs:
                full = os.path.join(pdir, pname)
                if os.path.isfile(full):
                    # It exists and is a file.
                    _libpath = os.path.abspath(full)
                    raise Found()
    except Found:
        pass

if _libpath is None:
    raise Exception("cannot locate walletlib")
else:
    _libpath = _libpath
    #print("Found library file at '{}'".format(_libpath))

_lib = ctypes.CDLL(_libpath)

# Used for turning datetime.datetime objects into the
# seconds-since-midnight-1970-01-01 format needed for getBlockByTime.
EPOCH = datetime(1970, 1, 1)

# Used by the Request class.
COMPLETE = "Complete"


################################################################################
# These represent opaque types that the native library uses.

class sessiontoken(ctypes.Structure):
    pass


class reqtoken(ctypes.Structure):
    pass


################################################################################
# Internal functions

def _cstr(s):
    if s is None:
        return s
    else:
        return s.encode('utf-8')


def _pystr(s):
    if s is None:
        return s
    else:
        return s.decode('utf-8')


################################################################################
# Fix up the definitions of the functions.

_lib.setLoggingFileDescriptor.argtypes = [ctypes.c_int]
_lib.setLoggingFileDescriptor.restype = ctypes.c_int

_lib.walletLibraryVersion.argtypes = []
_lib.walletLibraryVersion.restype = ctypes.c_char_p

_lib.createNewWallet.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
_lib.createNewWallet.restype = ctypes.c_char_p

_lib.getWalletBytes.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_char_p]
_lib.getWalletBytes.restype = ctypes.c_char_p

_lib.getCacheBytes.argtypes = [ctypes.POINTER(sessiontoken)]
_lib.getCacheBytes.restype = ctypes.c_char_p

_lib.getFoundationBytes.argtypes = [ctypes.POINTER(sessiontoken)]
_lib.getFoundationBytes.restype = ctypes.c_char_p

_lib.createSession.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_char_p]
_lib.createSession.restype = ctypes.POINTER(sessiontoken)

_lib.getAddress.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
        ctypes.c_uint]
_lib.getAddress.restype = ctypes.c_char_p

_lib.getBalance.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_char_p]
_lib.getBalance.restype = ctypes.POINTER(reqtoken)

_lib.calculateTransferFee.argtypes = [ctypes.c_char_p]
_lib.calculateTransferFee.restype = ctypes.c_char_p

_lib.separateTransferAndFee.argtypes = [ctypes.c_char_p]
_lib.separateTransferAndFee.restype = ctypes.c_char_p

_lib.submitAdminInfo.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
    ctypes.c_char_p]
_lib.submitAdminInfo.restype = ctypes.POINTER(reqtoken)

_lib.submitPoeFeats.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
    ctypes.c_char_p]
_lib.submitPoeFeats.restype = ctypes.POINTER(reqtoken)

_lib.transfer.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
_lib.transfer.restype = ctypes.POINTER(reqtoken)


_lib.getBlockByIndex.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
        ctypes.c_uint, ctypes.c_int]
_lib.getBlockByIndex.restype = ctypes.POINTER(reqtoken)

_lib.getBlockByIdentifier.argtypes = [ctypes.POINTER(sessiontoken),
        ctypes.c_char_p, ctypes.c_int]
_lib.getBlockByIdentifier.restype = ctypes.POINTER(reqtoken)

_lib.getBlockByTime.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
        ctypes.c_uint, ctypes.c_int]
_lib.getBlockByTime.restype = ctypes.POINTER(reqtoken)

_lib.getLastBlock.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_uint,
        ctypes.c_int]
_lib.getLastBlock.restype = ctypes.POINTER(reqtoken)

_lib.getTransaction.argtypes = [ctypes.POINTER(sessiontoken), ctypes.c_char_p]
_lib.getTransaction.restype = ctypes.POINTER(reqtoken)

_lib.freeSession.argtypes = [ctypes.POINTER(sessiontoken)]
_lib.freeSession.restype = None


_lib.getRequestStatus.argtypes = [ctypes.POINTER(reqtoken)]
_lib.getRequestStatus.restype = ctypes.c_int

_lib.getRequestResultCount.argtypes = [ctypes.POINTER(reqtoken)]
_lib.getRequestResultCount.restype = ctypes.c_uint

_lib.getRequestResult.argtypes = [ctypes.POINTER(reqtoken)]
_lib.getRequestResult.restype = ctypes.c_char_p

_lib.freeRequest.argtypes = [ctypes.POINTER(reqtoken)]
_lib.freeRequest.restype = None

################################################################################
# The classes and functions.

MAINNET = 0
TESTNET = 1

ZONE_AFRICA         = 0x10  # Sub-Saharan Africa
ZONE_AMERICA_LATIN  = 0x20  # Latin America and the Caribbean
ZONE_AMERICA_NORTH  = 0x30  # North America
ZONE_ASIA_EAST      = 0x40  # East Asia and Pacific
ZONE_ASIA_SOUTH     = 0x50  # South Asia
ZONE_EUROPE         = 0x60  # Europe and Central Asia
ZONE_MIDDLEEAST     = 0x70  # Middle East and North Africa

def set_logging(file_descriptor):
    return (_lib.setLoggingFileDescriptor(file_descriptor) != 0)

def wallet_library_version():
    return _pystr(_lib.walletLibraryVersion());

def new_wallet(auth_bytes, passphrase):
    '''
    Creates a new wallet. Returns the raw bytes representing it.
    '''
    rval = _lib.createNewWallet(_cstr(auth_bytes), _cstr(passphrase))
    if rval is None:
        raise Exception("new_wallet failed, no further information available")
    return rval


class Session:
    def __init__(self, cache, wallet, passphrase):
        '''
        Creates a new session.

        'cache' can be None, the raw cache bytes, or a filename to read them
        from. If the file doesn't exist, the function will use None for it.

        'wallet' can be the raw wallet bytes (from new_wallet() or elsewhere)
        or a filename. If it's a filename, it must exist and be readable.

        'passphrase' must be the passphrase that was used when the wallet was
        created or written.

        This function will return almost immediately, and the session will be
        created in the background.
        '''
        if cache is not None and not isinstance(cache, bytes):
            if os.path.isfile(cache):
                with open(cache) as f:
                    cache = f.read()
            else:
                cache = None

        if wallet is None:
            raise Exception("'None' is not valid for the wallet, you must "
                    "provide a filename or the result from new_wallet()")
        elif not isinstance(wallet, bytes):
            if os.path.isfile(wallet):
                with open(wallet, 'rb') as f:
                    wallet = f.read()
            else:
                raise Exception("'{}' is not a valid wallet file"
                        .format(str(wallet)))

        self.session = _lib.createSession(cache, wallet, _cstr(passphrase))
        if self.session is None:
            raise Exception("could not create session; is passphrase correct?")

    def __del__(self):
        '''
        Just ensures that the self.close() function is called when objects of
        this class are garbage-collected.
        '''
        self.close()

    def calculate_transfer_fee(self, value):
        '''
        Calculates the fee for a transfer of a specific size.
        '''
        rval = _lib.calculateTransferFee(_cstr(str(value)))
        if rval is not None:
            rval = int(rval)
        return rval

    def separate_transfer_and_fee(self, value):
        '''
        Given a combined transfer and fee (in toshi), separates it into the
        transfer portion and the fee portion.
        '''
        rval = _lib.separateTransferAndFee(_cstr(str(value)))
        if rval is not None:
            rvalues = rval.split(b',')
            rval = (int(rvalues[0]), int(rvalues[1]))
        return rval

    def close(self):
        '''
        Closes the session. You cannot use the session object after calling
        this.
        '''
        if self.__dict__.get('session') is not None:
            _lib.freeSession(self.session)
        self.session = None

    def _check(self):
        if self.session is None:
            raise Exception("This Session object has been closed.")

    def write_cache(self, filename):
        '''
        Writes the cache bytes to a new file. Doesn't try to back up the
        previous one, since nothing is lost if the cache is deleted except
        possibly a few seconds.
        '''
        self._check()

        cachebytes = _lib.getCacheBytes(self.session)
        if cachebytes is not None:
            with open(filename, 'w') as f:
                f.write(cachebytes)

    def write_wallet(self, filename, passphrase):
        '''
        Writes the wallet bytes to a new file, after creating a backup of the
        existing one (just in case).
        '''
        self._check()

        backup = filename + ".bak"
        if os.path.exists(filename):
            has_backup = True
            try:
                os.remove(backup)
            except FileNotFoundError:
                pass
            os.rename(filename, backup)
        else:
            has_backup = False

        walletbytes = _lib.getWalletBytes(self.session, _cstr(passphrase))
        if walletbytes is None:
            raise Exception("could not retrieve wallet bytes")

        try:
            with open(filename, 'w') as f:
                f.write(walletbytes)

            # The file was successfully written, so we can delete the backup.
            if has_backup:
                os.remove(backup)
        except Exception as e:
            if has_backup:
                try:
                    os.remove(filename)
                except FileNotFoundError:
                    pass
                os.rename(backup, filename)
                raise Exception("write_wallet failed ('{}'), previous wallet "
                    "file restored".format(e))
            else:
                raise Exception("write_wallet failed ('{}')".format(e))

    def get_foundation_bytes(self):
        '''
        Returns the raw wallet bytes, encrypted to the Shakti Foundation's
        current public key and an ephemeral private key. This function is
        intended solely for the Shakti Foundation's use, as the information it
        provides is useless to anyone who doesn't have access to the Shakti
        Foundation's private key.
        '''
        return _pystr(_lib.getFoundationBytes(self.session))

    def get_address(self, pocket_index, network = MAINNET):
        '''
        Retrieves the address for a specific pocket, or None if the requested
        address doesn't exist.
        '''
        return _pystr(_lib.getAddress(self.session, pocket_index, network))

    def get_all_addresses(self, network=MAINNET):
        '''
        Retrieves all active addresses for this wallet and network.
        '''
        rval = []
        index = 0
        while True:
            r = self.get_address(index, network)
            if r is None:
                break
            rval += [r]
            index += 1
        return rval

    def get_balance(self, pocket_address):
        '''
        Starts the retrieval of the balance for any pocket (not limited to the
        ones for this wallet). Works for both mainnet and testnet addresses.

        This returns a Request. See the Request class for details.

        On success, the result will be a string representing the number of
        toshi in the pocket. (A toshi is 1/100,000th of a coin.)

        On failure, the result may be an (English) error message.
        '''
        return Request(_lib.getBalance(self.session, _cstr(pocket_address)))

    def submit_admin_info(self, network, commands):
        '''
        Submits one or more administrative information commands. The session
        must be started with a key that the network recognizes as being
        authorized to give such commands (generally reserved for the Shakti
        Foundation).

        The function returns a Request. See the Request class for details. When
        complete, the status will be one of those given for the transfer()
        function, including `2` for included-in-block.
        '''
        return Request(_lib.submitAdminInfo(self.session, network,
            _cstr(commands)), complete=2)

    def submit_poe_feats(self, zone_and_network, feat_declarations):
        '''
        Submits one or more feat declarations. The session must be started with
        a key that the network recognizes as being authorized to sign feat
        declarations (generally reserved for school officials and related
        authorities).

        The function returns a Request. See the Request class for details. When
        complete, the status will be one of those given for the transfer()
        function, including `2` for included-in-block.
        '''
        return Request(_lib.submitPoeFeats(self.session, zone_and_network,
            _cstr(feat_declarations)), complete=2)

    def transfer(self, source_pocket_index, target_pocket, value_in_toshi,
            fee_in_toshi = None, memo = None):
        '''
        Transfers coin from the source_pocket_index (usually zero, for the
        primary pocket) to the target_pocket address. The network (mainnet or
        testnet) is determined by the target_pocket's address.

        value_in_toshi, as the name suggests, is the amount of coin to transfer
        in toshis (a toshi is 1/100,000th of a coin). This can be provided as a
        string or a numeric value.

        fee_in_toshi is the fee returned by the calculate_transfer_fee()
        function, or None to let the library determine the fee.

        The 'memo' item is for a planned future feature. It is passed to the
        native library, but at the time of this writing, the library does
        nothing with it.

        The function returns a Request. See the Request class for details. When
        complete, the status will be one of the following:
            * -101: lost connection, transaction status unknown
            * -52: transaction failed, not acknowledged, you can try again
            * -51: transaction failed, transient error, you can try again
            * -11: transaction failed, fee information incorrect
            * -10: transaction failed, bad or duplicate identifier
            * -9: transaction failed, future timestamp
            * -8: transaction failed, repeated timestamp
            * -7: transaction failed, insufficient funds
            * -6: transaction failed, bad transaction signature
            * -5: transaction failed, less than minimum
            * -4: transaction failed, currency mismatch
            * -3: transaction failed, sent to sending address
            * -2: transaction failed, sent to faucet
            * -1: general failure, transaction status unknown
            * 0: transaction still being processed
            * 1: transaction approved and should be included in the next block
                * NOTE: this is NOT a final value, you should get a second
                  value (either `2` or an error) as well.
            * 2: transaction included in block

        You can retrieve the result (which will be the transaction ID) from the
        returned token at any time. Note that a transaction ID does not
        guarantee that the transaction went through, it only identifies the
        transaction.

        Note that the returned status is not definitive unless it returns `2`.
        See the documentation of the native library for details.
        '''
        if fee_in_toshi is None:
            fee_in_toshi = self.calculate_transfer_fee(value_in_toshi)

        return Request(_lib.transfer(self.session, source_pocket_index,
            _cstr(target_pocket), _cstr(str(value_in_toshi)),
            _cstr(str(fee_in_toshi)), _cstr(memo)), complete=2)

    def get_block(self, index_or_identifier, include_transaction_details=False,
            network=MAINNET):
        '''
        Requests a specific block of the mainnet or testnet blockchains.

        'index_or_identifier' can be a non-negative number to request by index
        (index zero is the genesis block for the blockchain in question). If
        you request by index, this function uses the 'network' parameter to
        decide which network to request it for, MAINNET (the default) or
        TESTNET.

        'index_or_identifier' may also be a datetime.datetime value, to request
        the block that was created on or immediately after that time. If there
        is no such block, the latest block is returned instead. When used this
        way, the function uses the 'network' parameter to decide which network
        to request it for, MAINNET (the default) or TESTNET.

        'index_or_identifier' can also be a string, in which case the function
        will request a block with that identifier. The network is implied by
        the identifier, the 'network' parameter is ignore in this case.

        Finally, 'index_or_identifier' can be -1 to request the most recent
        block on the blockchain 'network'. Use this to see how many blocks are
        available at present.

        This function returns a Request. See the Request class for details. On
        success, the result will be a JSON-formatted string with the requested
        details.
        '''
        if isinstance(index_or_identifier, str):
            return Request(_lib.getBlockByIdentifier(self.session,
                _cstr(index_or_identifier), include_transaction_details))
        elif isinstance(index_or_identifier, datetime):
            return Request(_lib.getBlockByTime(self.session,
                network, int((index_or_identifier - EPOCH).total_seconds()),
                include_transaction_details))
        elif index_or_identifier == -1:
            return Request(_lib.getLastBlock(self.session, network,
                include_transaction_details))
        else:
            return Request(_lib.getBlockByIndex(self.session, network,
                index_or_identifier, include_transaction_details))

    def get_transaction(self, identifier):
        '''
        This function returns a Request. See the Request class for details. On
        success, the result will be a JSON-formatted string with the requested
        details.
        '''
        return Request(_lib.getTransaction(self.session, _cstr(identifier)))


class Request:
    def __init__(self, req, complete = 1):
        '''
        Initializes a Request. This must be done through certain Session member
        functions.
        '''
        if not isinstance(req, ctypes.POINTER(reqtoken)):
            raise Exception("a Request object can only be generated with a "
                    "reqtoken value")
        self.token = req
        self.complete = complete

    def __del__(self):
        self.close()

    def close(self):
        '''
        Once you're done with a token, you can either close it or just dispose
        of the handle.
        '''
        if self.token is not None:
            _lib.freeRequest(self.token)
        self.token = None

    def status(self):
        '''
        This will return an integer representing the status of this request. It
        will be a positive number to indicate that the request has completed a
        step successfully, zero to indicate that the request isn't yet
        complete, or a negative number to indicate that there was an error.

        The actual values and their meanings depend on the request, but an
        error code of -101 always means that the library lost its connection to
        the server so the status of the request is unknown.
        '''
        return _lib.getRequestStatus(self.token)

    def count(self):
        '''
        Some requests can return multiple results. Once status() returns a
        positive value, you can call this to see how many results have been
        returned.
        '''
        return _lib.getRequestResultCount(self.token)

    def result(self, whichresult = None, wait = False):
        '''
        Returns the actual results of the request.

        You'll often need to wait until status() either returns a positive
        value, or returns a specific positive value indication completion
        (though some calls provide data before that). To wait for any positive
        value, set the `wait` parameter to any true value (we suggest `True`).
        To wait until the call is complete, set it to `walletlib.Complete`.

        It will return a single value if there's only one result, or if a
        specific result is requested; if multiple results are returned and you
        don't ask for a specific result, it will return a list of all of them.
        '''
        # Is the result ready?
        status = self.status()
        if wait == COMPLETE:
            while not (status < 0 or status == self.complete):
                time.sleep(0.01)
                status = self.status()
        elif wait:
            while status == 0:
                time.sleep(0.01)
                status = self.status()

        if whichresult is None:
            # How many results are there?
            count = self.count()
            if count == 0:
                return None
            if count == 1:
                return _pystr(_lib.getRequestResult(self.token, 0))

            rval = []
            for r in range(count):
                rval += [ _pystr(_lib.getRequestResult(self.token, r)) ]
            return rval
        else:
            return _pystr(_lib.getRequestResult(self.token, whichresult))


# Some example code.
def main():
    otheraddrs = [
        'sxe-t#1gr9bnuk03h05myatgr80n5errxg6qfuyjd51',
        'sxe-t#1gq29bm8y8nd7nn4adw0a67y2jugy0nm6vbqi',
        'sxe-t#1669nu27p3qqjf0bpm59xba332v5qminvyd9v',
    ]

    # Check the wallet library version.
    print("Wallet library version... ", end='')
    print(wallet_library_version())

    # Create a wallet. If this fails, it will raise an exception.
    print("Creating new wallet...")
    wallet = new_wallet(None, "passphrase")

    # Create a session. If this fails, it will raise an exception.
    print("Creating session...")
    session = Session(None, wallet, "passphrase")

    print()  # empty line

    # Get all of the testnet addresses for this wallet.
    addrs = session.get_all_addresses(TESTNET)

    # Request the balances of each of them. There's a function in the native
    # library that will return these with fewer steps (getWalletBalance), but
    # this way provides more information and demonstrates some key points.
    requests = { }
    for a in addrs:
        print("Requesting balance for this wallet's address {}...".format(a))
        requests[a] = session.get_balance(a)
    print()  # empty line

    # Request the balances of a few other addresses, just to hammer home that
    # the system can handle multiple requests simultaneously.
    for a in otheraddrs:
        print("Requesting balance for other address {}...".format(a))
        requests[a] = session.get_balance(a)
    print()  # empty line

    # The balances are being requested in the background. We'll need to wait
    # until they're returned.
    for a in addrs + otheraddrs:
        while True:
            ready = requests[a].status()
            if ready != 0:
                break
            else:
                # Not ready yet. We'll put a small delay in here, 1/100th of a
                # second (10 milliseconds). You could also continue working on
                # other things and come back to this later. We strongly
                # recommend that you DON'T just keep calling the status
                # function repeatedly without a delay, as it keeps the CPU
                # straining at maximum.
                time.sleep(0.01)

        # We have a result.
        if ready < 0:
            print("Error requesting balance for {}".format(a))
        else:
            print("Balance for {} is {} toshi".format(a, requests[a].result()))

        # Free the request, releasing its resources. Technically you don't
        # *have* to do this, but if you don't you'll have a memory leak, which
        # will eventually be fatal to a long-running process.
        requests[a].close()
        del requests[a]

    print()  # empty line

    # Request the last testnet block. This demonstrates a few other features,
    # like the built-in wait ability of Request.result().
    req = session.get_block(-1, network=TESTNET)
    print("The most recent testnet block is: {}".format(req.result(wait=True)))

    # Close the session, to free its resources. As with requests, this isn't
    # technically required, but you'll want to do this when you're done with
    # the wallet library in order to prevent memory leaks.
    session.close()
    return True


if __name__ == "__main__":
    # This is being called as a program, rather than loaded as a library. Call
    # the example code.
    #
    # Note that zero is the no-problems exit code for programs, and anything
    # else is supposed to indicate an error.
    try:
        sys.exit(0 if main() else 1)
    except Exception as e:
        print("Exception: {}".format(e))
        sys.exit(1)
