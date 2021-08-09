#!/usr/bin/env python3

import os
import sys
import time
import base64
import traceback
import walletlib

import builtins
import csv


SOURCE_WALLETBYTES = b"5r9A5A/xxCbyqn0KCG41JlogMpNLPlemI3E4m2izWm8/rhSUteQfOKIJbdbHLgtpMKAxo58XuZK48i7sFcnojVrIhSIRyc0k8F2LPxnOKqcA1M0FiYLclExkqmxOtIOELKIFF0GLrKPB/xl7KQgUZ72iiVAmXwrdfO59w7n1kQTSWlJXRD9qp2hQiZn6OHAMuFQ8jMlf/d6sq/MK7osh5w"
TARGET_WALLETBYTES_FILE = os.path.expanduser('test-shakti.data')
MAX_WALLET_COUNT = 32
FEE_POCKET = "sxe-t#1miubu9f8j2wbta25ujjxjpnk944d134ar6dm"

TOSHI_PER_CHAI = 1000
CHAI_PER_COIN = 100
TOSHI_PER_COIN = CHAI_PER_COIN * TOSHI_PER_CHAI
MINIMUM_TRANSFER = 6_000  # five chai, in toshi, plus minimum fee of 1 chai


def read_target_walletbytes(filepath, requested_wallet_count):
    if not os.path.exists(filepath):
        # Create a number of target wallets, and write the encoded wallet bytes
        # to the data file, one per line.
        with open(filepath, 'w') as f:
            for i in range(MAX_WALLET_COUNT):
                w = walletlib.new_wallet(None, None)
                f.write(base64.b64encode(w).decode('utf-8') + '\n')

    rval = []
    with open(filepath) as f:
        for line in f:
            rval.append(base64.b64decode(line))

    if len(rval) < MAX_WALLET_COUNT:
        print("Found {} wallets, need {}; adding new ones".format(len(rval),
                                                                  MAX_WALLET_COUNT))
        with open(filepath, 'a') as f:
            while len(rval) < MAX_WALLET_COUNT:
                w = walletlib.new_wallet(None, None)
                line = base64.b64encode(w).decode('utf-8')
                f.write(line + '\n')
                rval.append(base64.b64decode(line))
        print("Now has {} wallets".format(len(rval)))
    else:
        print("Read {} sets of wallet bytes".format(len(rval)))

    # If this fails, you may need to delete the data file and let the code
    # recreate it.
    if len(rval) < MAX_WALLET_COUNT:
        raise RuntimeError("Expected {} groups of walletbytes in file, found {}\n"
                           "Delete file '{}' and let the program recreate it."
                           .format(MAX_WALLET_COUNT, len(rval), filepath))
    return rval[:requested_wallet_count]


class Task:
    def __init__(self, extra=None):
        self.extradata = extra

    def done(self, *, wait=False):
        '''
        Override this.
        '''
        return True

    def result(self):
        return None

    def extra(self):
        return self.extradata


class RequestTask(Task):
    '''
    For all requests except transfers.
    '''

    def __init__(self, request, callafter=None, continue_values=frozenset([0]),
                 extra=None):
        '''
        The callafter object takes one parameter (the RequestTask object or a
        derivative). It must return None or another Task or derivative; if it
        does not return None, then the Task it returns is treated as part of
        this one.
        '''
        Task.__init__(self, extra)
        self.req = request
        self.next_task = None
        self.cont = continue_values
        self.callafter = callafter
        self.donecode = None

    def done(self, *, wait=False):
        if wait:
            code = self.done()
            while code is None:
                time.sleep(0.01)
                code = self.done()
            return code

        if self.donecode is not None:
            # We've previously finished this task.
            if self.donecode > 0:
                if self.next_task is not None:
                    return self.next_task.done()
            return self.donecode

        d = self.req.status()
        if d in self.cont:
            # We're not done yet.
            return None

        # Finish this step now.
        self.donecode = d
        if self.donecode > 0 and self.callafter is not None:
            self.next_task = self.callafter(self)
            if self.next_task is not None:
                # There's another task.
                return self.next_task.done()
        return self.donecode

    def status(self):
        'An alias for done().'
        return self.done()

    def result(self):
        if self.donecode is not None:
            if self.next_task is not None:
                return self.next_task.result()
        return self.req.result()

    def identifier(self):
        return None


class TransferTask(RequestTask):
    '''
    Only for transfers.
    '''

    def __init__(self, request, callafter=None, extra=None):
        RequestTask.__init__(self, request, callafter, continue_values=[0, 1],
                             extra=extra)

    def identifier(self):
        rval = self.req.result()
        if rval is not None and not isinstance(rval, str):
            print("*** WARNING! WARNING! WARNING! ***")
            print("Expected string response, got this:")
            print("{}".format(rval))
        return rval


def wait_until_complete(tasks):
    successful = True
    if isinstance(tasks, list):
        for req in tasks:
            if req.done(wait=True) < 0:
                successful = False
    else:
        if tasks.done(wait=True) < 0:
            successful = False
    return successful


def wait_for_network():
    '''
    After a successful transfer, it can take a couple seconds before all of the
    nodes in the network know about it.
    '''
    time.sleep(2)


class Pocket:
    def __init__(self, session, *, balance=None):
        self.session = session
        self._balance = balance
        self._balancereq = None
        self._balancereqfailed = 0
        self._transferreq = []

    def pocketid(self):
        return self.session.get_address(0, walletlib.TESTNET)

    def balance(self):
        if self._balance is None:
            # Assume that there's a transient network problem. Delay for an
            # ever-increasing amount of time, attempting to retrieve the
            # balance after each, before finally giving up.
            for delay in [1, 5, 10, 30, 60, 90]:
                time.sleep(delay)
                self.balance_request_start(wait=True, raise_on_failure=False)
                if self._balance is not None:
                    break
        return self._balance

    def update_balance(self, *, wait=True, raise_on_failure=True):
        self.balance_request_start(wait=wait, raise_on_failure=raise_on_failure)

    def balance_request_start(self, *, wait=False, raise_on_failure=True,
                              if_needed=False):
        if if_needed:
            if self._balance is not None:
                return None

        if self._balancereq is None:
            self._balance = None
            self._balancereq = RequestTask(self.session.get_balance(
                self.pocketid()))

        if wait:
            self.balance_request_finish(raise_on_failure=raise_on_failure)
            return None
        else:
            return self._balancereq

    def balance_request_finish(self, *, raise_on_failure=True):
        if self._balancereq is not None:
            r = self._balancereq.done(wait=True)
            if r < 0:
                if not raise_on_failure:
                    self._balance = None
                    self._balancereq = None
                    self._balancereqfailed = 0
                    return None

                self._balancereqfailed += 1
                if self._balancereqfailed >= 3:
                    raise AssertionError("failed to retrieve balance on pocket "
                                         + self.pocketid())
                else:
                    return self.balance_request_start(wait=True)

            # Otherwise it was successful.
            self._balance = int(self._balancereq.result())
            self._balancereq = None
            self._balancereqfailed = 0

    def transfer_start(self, tgtpocket, amount_in_toshi=None,
                       fee_in_toshi=None, timestamp=None, *, finish=None):
        if amount_in_toshi is None:
            # Transfer everything.
            amount_in_toshi, fee_in_toshi = self.session.separate_transfer_and_fee(
                self.balance())

        xfer = TransferTask(self.session.transfer(0, tgtpocket.pocketid(),
                                                  amount_in_toshi, fee_in_toshi), extra=tgtpocket)
        self._transferreq.append(xfer)
        self._balance = None
        tgtpocket._balance = None
        self.timestamp = timestamp

        if finish is not None:
            return self.transfer_finish(raise_assertion=finish)
        else:
            return xfer

    def transfer_finish(self, raise_assertion=True):
        failed = []
        rval = self._transferreq
        for t in rval:
            r = t.done(wait=True)
            if r < 0:
                failed.append("transfer {} to pocket {} failed, error code {}"
                              .format(t.identifier(), t.extra().pocketid(), r))
        if failed and raise_assertion:
            raise AssertionError('\n'.join(failed))
        self._transferreq = []
        return rval

    def __str__(self):
        return self.pocketid()

    def __repr__(self):
        return "<Pocket " + self.pocketid() + ">"


class MultiPocket:
    @staticmethod
    def update_balances(pockets):
        try:
            pockets = iter(pockets)
        except TypeError:
            # It's not iterable. It has to be a single pocket or session.
            pockets = [pockets]
            pockets = iter(pockets)

        for p in pockets:
            p.balance_request_start()
        for p in pockets:
            p.balance_request_finish()


def as_coin(toshi):
    try:
        # Split it into coin and chai. The toshi portion is currently ignored.
        total_in_chai = int(int(toshi) / TOSHI_PER_CHAI)
        coin = total_in_chai // CHAI_PER_COIN
        chai = total_in_chai % CHAI_PER_COIN

        # Insert punctuation into the coin portion, because some of these
        # numbers get pretty big and hard for the eye to deal with.
        coinstr = str(coin)
        coinparts = []
        while len(coinstr) != 0:
            coinparts.append(coinstr[-3:])
            coinstr = coinstr[:-3]
        coinstr = ','.join(coinparts[::-1])

        # Return the formatted value.
        return "{}.{:02}".format(coinstr, chai)
    except:
        # Just leave the input value as it was.
        print("WARNING: as_coin() given '{}', can't convert".format(toshi))
        return toshi + " toshi"


class TestCSV:
    DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'
    TIME_FORMAT = '%H:%M:%S'

    class TransferRecord:
        def __init__(self, from_name, to_name, amount, fee, tx, txid, timestamp):
            self.from_name = from_name
            self.to_name = to_name
            self.amount = amount
            self.fee = fee
            self.tx = tx
            self.txid = txid
            self.timestamp = timestamp

    def __init__(self, title, csvfilename, names_and_sessions, feepocket):
        self.successful = False  # until it actually *is* successful

        self.addrs = names_and_sessions
        self.feepocket = feepocket
        self.feesession = walletlib.Session(None, SOURCE_WALLETBYTES, '')
        self.transfer_records = []

        # Open the CSV file and set up the CSV writer.
        self.rawcsvfile = open(csvfilename, 'w', newline='')
        self.csvwriter = csv.writer(self.rawcsvfile)
        self.csvcolumns = ['step', 'txid', 'timestamp']
        for pname in self.addrs.keys():
            self.csvcolumns.append(pname)
        self.csvcolumns.append('fee')
        self.csvline = None

        # Write out the header line, including the title.
        self.write('step', title)
        self.write('txid', "Transaction ID")
        self.write('timestamp', "Timestamp")
        self.write('fee', "Fee Pocket\n{}".format(feepocket))
        for key, value in self.addrs.items():
            self.write(key, "{}\n{}".format(key, value.pocketid()))
        self.write()

        # Write out the initial balances for each account.
        self.expected_balances = {}
        self.write('step', "Initial Balances")
        for key in self.addrs.keys():
            self.expected_balances[key] = self.balance(key)
            self.write(key, as_coin(self.expected_balances[key]))
        self.expected_balances['fee'] = self.balance('fee')
        self.write('fee', as_coin(self.expected_balances['fee']))
        self.write()

        # Write out the starting time, in UTC.
        self.start = time.time()
        self.write('step', "Started at {} UTC".format(time.strftime(
            self.DATETIME_FORMAT, time.gmtime())))
        self.write()

    def __del__(self):
        # Write the final balances.
        self.write('step', "Final Balances")
        for key in self.addrs.keys():
            self.write(key, as_coin(self.balance(key, expected=True)))
        self.expected_balances['fee'] = self.balance('fee')
        self.write('fee', as_coin(self.balance('fee', expected=True)))
        self.write()

        # Write out the ending time, in UTC.
        reason = "Test Finished" if self.successful else "Test Interrupted"
        elapsed = int(time.time() - self.start)
        self.write('step', "{} at {} UTC".format(reason, time.strftime(
            self.DATETIME_FORMAT, time.gmtime())))
        self.write()

        # Write out the elapsed time. Since time.time() returns the current
        # time in seconds-since-epoch, that isn't difficult to calculate.
        seconds = elapsed % 60
        elapsed //= 60

        minutes = elapsed % 60
        elapsed //= 60

        hours = elapsed % 24
        elapsed //= 24

        days = elapsed

        if days:
            s = '{} days, {}:{:02}:{:02} hours'.format(days, hours, minutes,
                                                       seconds)
        elif hours:
            s = '{}:{:02}:{:02} hours'.format(hours, minutes, seconds)
        else:
            s = '{}:{:02} minutes'.format(minutes, seconds)

        self.write('step', 'Elapsed time: {}'.format(s))
        self.write()

    def mark_successful(self):
        self.successful = True

    def write(self, name=None, value=None, *, flush=True):
        if self.csvline is None:
            self.csvline = ['' for fieldcount in range(len(self.csvcolumns))]

        if name is None:
            # Write out the line.
            self.csvwriter.writerow(self.csvline)
            if flush:
                self.rawcsvfile.flush()
            self.csvline = None
        else:
            # Find the field index and write the new value to it.
            idx = self.csvcolumns.index(name)
            if self.csvline[idx] != '':
                raise ValueError("Tried to write to field '{}', but it already had a value"
                                 .format(name))
            self.csvline[idx] = str(value)

    def balance(self, name, *, update=False, expected=False):
        if expected:
            return self.expected_balances[name]
        elif name == 'fee':
            r = self.feesession.get_balance(self.feepocket)
            try:
                return int(r.result(wait=True))
            except (ValueError, TypeError):
                return self.balance(name, update=update, expected=expected)
        else:
            session = self.addrs[name]
            if update:
                session.update_balance()
            return session.balance()

    def transfer(self, fromname, toname, amount, fee=None):
        if fee is None:
            fee = self.feesession.calculate_transfer_fee(amount)
        fromsession = self.addrs[fromname]
        tosession = self.addrs[toname]
        tx = fromsession.transfer_start(tosession, amount, fee)
        timestamp = time.strftime(self.TIME_FORMAT, time.gmtime())
        transfer = self.TransferRecord(fromname, toname, amount, fee, tx,
                                       tx.result(), timestamp)
        if tx.result() is not None:
            self.transfer_records.append(transfer)
        else:
            print("transfer ID is None, skipping")

    def finish_transfers(self, *, check_balances=False, raise_on_error=True):
        wait_for_network()
        requests = []
        for session in self.addrs.values():
            requests += session.transfer_finish(raise_assertion=False)

        delayed = False
        for tx in self.transfer_records:
            print("  checking status code for {}".format(tx.txid))
            status = tx.tx.status()
            if status == -1 or status == -101:
                print("  {} returned possible-failure code {}".format(tx.txid,
                                                                      status))

                # Wait a bit, to give it time to go through if it's going to.
                # We only have to do this once per transfer group.
                if not delayed:
                    print("  delaying...")
                    delayed = True
                    time.sleep(90)

                # See if it actually failed.
                req = self.feesession.get_transaction(tx.txid)
                while req.status() == 0:
                    time.sleep(0.01)
                if req.status() < 0:
                    print("  {} really failed".format(tx.txid))
                    failcode = status
                else:
                    print("  {} actually succeeded".format(tx.txid))
                    failcode = None

            elif status < 0:
                print("  {} returned failure code {}".format(tx.txid, status))
                failcode = status
            else:
                # Otherwise it was successful
                print("  {} was successful".format(tx.txid))
                failcode = None

            if failcode is None:
                self.expected_balances[tx.from_name] -= tx.amount + tx.fee
                self.expected_balances[tx.to_name] += tx.amount
                self.expected_balances['fee'] += tx.fee

            self.write('step', "Transfer {} from {} to {}, fee {}"
                       .format(as_coin(tx.amount), tx.from_name, tx.to_name,
                               as_coin(tx.fee)))
            if failcode:
                self.write('txid', '{} (FAILED, error code {})'.format(tx.txid,
                                                                       failcode))
                self.write('timestamp', tx.timestamp)
                self.write(tx.from_name, "unchanged")
                self.write(tx.to_name, "unchanged")
                self.write('fee', "unchanged")
            else:
                self.write('txid', tx.txid)
                self.write('timestamp', tx.timestamp)
                self.write(tx.from_name, as_coin(self.expected_balances[tx.from_name]))
                self.write(tx.to_name, as_coin(self.expected_balances[tx.to_name]))
                self.write('fee', as_coin(self.expected_balances['fee']))
            self.write()

        self.transfer_records = []

        if check_balances:
            # To speed things up, request all of the required balances here.
            # Then they should be ready when we ask for them below.
            for name, session in self.addrs.items():
                session.balance_request_start(if_needed=True)

            errors = []
            for name, session in self.addrs.items():
                b = session.balance()
                if b != self.expected_balances[name]:
                    errors.append("ERROR: balance for '{}' expected to be {}, but found {}"
                                  .format(name, self.expected_balances[name], b))
            if errors:
                if raise_on_error:
                    raise AssertionError('\n'.join(errors))
                else:
                    # Just show the error messgaes.
                    print('\n'.join(errors))

            # The fee pocket's balance can change without warning, if other
            # transactions are going on (and they usually will be). We'll just
            # check that it's greater than or equal to what we expect.
            feebalance = self.balance('fee')
            if feebalance < self.expected_balances['fee']:
                print("WARNING: balance for fee account expected to be {} or greater, but found {}"
                      .format(feebalance, self.expected_balances['fee']))


def run_multitransfer_test_round(rnum, srcpocket, tgtpockets):
    TEST_TRANSFER_AMOUNT_IN_TOSHI = 2 * TOSHI_PER_COIN

    print("\nStarting round {} of testing...".format(rnum + 1))

    # Make sure the target pockets are empty. If there's anything greater than
    # the minimum transfer amount (plus fees) in any of them, chuck it back
    # into the source account.
    print("\nRequesting balances from target pockets...")
    MultiPocket.update_balances(tgtpockets)

    transfers = []
    for p in tgtpockets:
        if p.balance() >= MINIMUM_TRANSFER:
            xfer, fee = srcpocket.session.separate_transfer_and_fee(p.balance())
            print("Balance of {} in target pocket {}, transferring to source pocket"
                  .format(p.balance(), p))
            p.transfer_start(srcpocket, xfer, fee)
            transfers.append(p)
            p.initialbalance = 0
        elif p.balance() > 0:
            print("Balance of {} in target pocket {}, too small to transfer"
                  .format(p.balance(), p))
            p.initialbalance = p.balance()
        else:
            # The balance was zero, as expected. Just note it.
            p.initialbalance = 0
    if len(transfers) != 0:
        for p in transfers:
            p.transfer_finish()
        wait_for_network()
        print("Initial transfers complete.")
    else:
        print("All initial balances are zero, continuing.")
    print()

    # Calculate how much coin we're going to need for the test, and check the
    # source pocket to make sure there's enough for the round.
    needed = TEST_TRANSFER_AMOUNT_IN_TOSHI * len(tgtpockets)

    print("Requesting source balance ({})...".format(srcpocket))
    srcpocket.update_balance()
    if srcpocket.balance() < needed:
        raise AssertionError("Source balance of {} toshi is less than the needed amount of {}."
                             .format(srcpocket.balance(), needed))
    print("Source balance of {} toshi is sufficient (need {}).".format(
        srcpocket.balance(), needed))

    # Transfer coin to each of the target pockets.
    for p in tgtpockets:
        print("Transferring {} toshi to {}...".format(
            TEST_TRANSFER_AMOUNT_IN_TOSHI, p))
        xfer, fee = srcpocket.session.separate_transfer_and_fee(
            TEST_TRANSFER_AMOUNT_IN_TOSHI)
        identifier = srcpocket.transfer_start(p, xfer, fee).identifier()
        print("  Identifier {}".format(identifier))
    print()
    srcpocket.transfer_finish()
    wait_for_network()

    # Check the balances.
    expected_amount = srcpocket.session.separate_transfer_and_fee(
        TEST_TRANSFER_AMOUNT_IN_TOSHI)[0]
    MultiPocket.update_balances(tgtpockets)

    err = []
    for p in tgtpockets:
        expected = p.initialbalance + expected_amount
        if p.balance() != expected:
            err.append("Target pocket {} should have a balance of {}, but has {}."
                       .format(p, expected, p.balance()))
    if err:
        raise AssertionError('\n'.join(err))

    # Transfer all of the coin back from each of the target pockets to the
    # source pocket.
    print("Transferring all coin back to source pocket.")
    transfers = []
    for p in tgtpockets:
        if p.balance() >= MINIMUM_TRANSFER:
            xfer, fee = srcpocket.session.separate_transfer_and_fee(p.balance())
            print("Balance of {} in target pocket {}, transferring to source pocket"
                  .format(p.balance(), p))
            p.transfer_start(srcpocket, xfer, fee)
            transfers.append(p)
        elif p.balance > 0:
            print("Balance of {} in target pocket {}, too small to transfer"
                  .format(p.balance(), p))
        # Else the balance was zero, as expected. Just note it.
    if len(transfers) != 0:
        for p in transfers:
            p.transfer_finish()
        wait_for_network()
        print("Transfer-backs complete.")
    else:
        print("No transfer-backs needed?!")
    print()

    # Make sure the target pockets are all empty.
    print("Confirming transfer-backs.")
    MultiPocket.update_balances(tgtpockets)

    err = []
    for p in tgtpockets:
        if p.balance() != 0:
            err.append("Balance of {} in target pocket {} after transfer-backs"
                       .format(p.balance(), p))
    if err:
        raise AssertionError('\n'.join(err))

    return True


def set_balance(pocket, required_balance, frompocket=None):
    # See what the current balance is.
    bal = pocket.balance()
    if bal == required_balance:
        return

    # We'll put any excess coin in the SOURCE_WALLETBYTES, and pull any needed
    # coin from there as well.
    if frompocket is None:
        frompocket = Pocket(walletlib.Session(None, SOURCE_WALLETBYTES, ''))
    print("Current source balance: {} toshi".format(frompocket.balance()))
    assert (frompocket.balance() >= required_balance + MINIMUM_TRANSFER), \
        "Source pocket {} needs {}, only has {}".format(str(frompocket),
                                                        required_balance + MINIMUM_TRANSFER, frompocket.balance())

    if bal < required_balance:
        if required_balance - bal < MINIMUM_TRANSFER:
            # We don't quite have enough. Transfer the difference plus the
            # minimum transfer amount to it, then transfer the minimum amount
            # away from it.
            frompocket.transfer_start(pocket, required_balance - bal +
                                      MINIMUM_TRANSFER, finish=True)
            xfer, fee = pocket.session.separate_transfer_and_fee(
                MINIMUM_TRANSFER)
            pocket.transfer_start(frompocket, xfer, fee, finish=True)
            pocket.update_balance()
            assert pocket.balance() == required_balance, "{} != {}".format(
                pocket.balance(), required_balance)
        else:
            # If this doesn't throw an exception, then it succeeded.
            print("Transferring {} toshi...".format(required_balance - bal))
            frompocket.transfer_start(pocket, required_balance - bal,
                                      finish=True)
    else:  # bal > required_balance
        if bal - required_balance < MINIMUM_TRANSFER:
            # We have too much, but only by a bit. Transfer the minimum
            # transfer amount to the pocket, then transfer the difference away.
            frompocket.transfer_start(pocket, MINIMUM_TRANSFER, finish=True)
            bal += MINIMUM_TRANSFER - pocket.session.calculate_transfer_fee(
                MINIMUM_TRANSFER)

        xfer, fee = pocket.session.separate_transfer_and_fee(bal -
                                                             required_balance)
        pocket.transfer_start(frompocket, xfer, fee, finish=True)


def set_balances(pockets_and_required_balances, *, frompocket=None):
    # Make sure we have the from-pocket.
    if frompocket is None:
        frompocket = Pocket(walletlib.Session(None, SOURCE_WALLETBYTES, ''))

    # Make sure we have the balances for all of the pockets, asynchronously.
    # Otherwise the system will ask for them only when it needs them, and wait
    # for the responses individually.
    for pb in pockets_and_required_balances:
        pb[0].balance_request_start(if_needed=True)
    for pb in pockets_and_required_balances:
        pb[0].balance_request_finish()

    # See whether we have enough.
    going_to_source = 0
    coming_from_source = 0
    ordered = [[], []]  # first is going to the source, second is coming from
    for pb in pockets_and_required_balances:
        pocket = pb[0]
        bal = pocket.balance()
        required_balance = pb[1]
        if bal < required_balance:
            ordered[0].append(pb)
            change = required_balance - bal
            if change < MINIMUM_TRANSFER:
                change += 2 * pocket.session.calculate_transfer_fee(MINIMUM_TRANSFER)[1]
            else:
                change += pocket.session.calculate_transfer_fee(change)
            coming_from_source += change
        elif bal > required_balance:
            ordered[1].append(pb)
            change = bal - required_balance
            if change < MINIMUM_TRANSFER:
                change -= 2 * pocket.session.calculate_transfer_fee(MINIMUM_TRANSFER)[1]
            else:
                change -= pocket.session.calculate_transfer_fee(change)
            going_to_source += change
        # else no change is needed for this pocket.
    # if frompocket.balance() + going_to_source < coming_from_source:
    #     raise Exception("Insufficient source balance: need {} more toshi.".format(
    #         frompocket.balance() + going_to_source < coming_from_source))

    # Move the coin, in the order we determined above.
    txids = []
    for pb in ordered[0] + ordered[1]:
        pocket = pb[0]
        bal = pocket.balance()
        required_balance = pb[1]
        if bal < required_balance:
            if required_balance - bal < MINIMUM_TRANSFER:
                # We don't quite have enough. Transfer the difference plus the
                # minimum transfer amount to it, then transfer the minimum
                # amount away from it.
                txids.append(frompocket.transfer_start(pocket, required_balance
                                                       - bal + MINIMUM_TRANSFER))
                xfer, fee = pocket.session.separate_transfer_and_fee(
                    MINIMUM_TRANSFER)
                txids.append(pocket.transfer_start(frompocket, xfer, fee))
                # pocket.update_balance()
                # assert pocket.balance() == required_balance, "{} != {}".format(
                #    pocket.balance(), required_balance)
            else:
                # If this doesn't throw an exception, then it succeeded.
                # print("Transferring {} toshi...".format(required_balance - bal))
                txids.append(frompocket.transfer_start(pocket, required_balance
                                                       - bal))
        else:  # bal > required_balance
            if bal - required_balance < MINIMUM_TRANSFER:
                # We have too much, but only by a bit. Transfer the minimum
                # transfer amount to the pocket, then transfer the difference
                # away.
                txids.append(frompocket.transfer_start(pocket, MINIMUM_TRANSFER))
                bal += MINIMUM_TRANSFER - pocket.session.calculate_transfer_fee(
                    MINIMUM_TRANSFER)

            xfer, fee = pocket.session.separate_transfer_and_fee(bal -
                                                                 required_balance)
            txids.append(pocket.transfer_start(frompocket, xfer, fee))

    # This code will raise an exception if there's any error.
    for pb in ordered[0] + ordered[1]:
        pb[0].transfer_finish()

    # If any of the transfers failed to start, then txids will contain one or
    # more None values.
    failcount = 0
    for txid in txids:
        if txid is None:
            failcount += 1
    if failcount != 0:
        raise AssertionError("{} transfer(s) failed to start".format(failcount))


def pk_test_1(srcpocket, tgtpocket):
    INITIAL_AMOUNT_IN_COIN = 100000
    INITIAL_AMOUNT = INITIAL_AMOUNT_IN_COIN * TOSHI_PER_COIN
    TRANSFER_PER_STEP = 100 * TOSHI_PER_COIN
    FEE_PER_STEP = srcpocket.session.calculate_transfer_fee(TRANSFER_PER_STEP)
    CHECK_ITERATIONS = 10
    WALLET_COUNT = 2

    target_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE,
                                                 WALLET_COUNT)
    srcpocket = Pocket(walletlib.Session(None, target_walletbytes[0], ''))
    tgtpocket = Pocket(walletlib.Session(None, target_walletbytes[1], ''))

    set_balance(srcpocket, INITIAL_AMOUNT)
    set_balance(tgtpocket, 0)
    test = TestCSV('Test 1: Single Wallet to Single Wallet', 'test1.csv',
                   {'Wallet A': srcpocket, 'Wallet B': tgtpocket},
                   FEE_POCKET)

    while test.balance('Wallet A', expected=True) >= MINIMUM_TRANSFER:
        count = 0
        while (test.balance('Wallet A', expected=True) >= TRANSFER_PER_STEP and
               count < CHECK_ITERATIONS):
            count += 1
            test.transfer('Wallet A', 'Wallet B', TRANSFER_PER_STEP)
        test.finish_transfers(check_balances=True)

    test.mark_successful()
    return


def pk_test_2():
    INITIAL_AMOUNT_IN_COIN = 100000
    INITIAL_AMOUNT = INITIAL_AMOUNT_IN_COIN * TOSHI_PER_COIN
    TRANSFER_PERCENTAGE_PER_STEP = 5
    CHECK_ITERATIONS = 10
    WALLET_COUNT = 11

    target_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE,
                                                 WALLET_COUNT)
    pockets = [Pocket(walletlib.Session(None, wbytes, '')) for wbytes
               in target_walletbytes]

    set_balance(pockets[0], INITIAL_AMOUNT)
    for p in pockets[1:]:
        set_balance(p, 0)
    named = {
        'Wallet {}'.format(chr(ord('A') + n)): pockets[n]
        for n in range(len(pockets))
    }

    test = TestCSV('Test 2: Single Wallet to Multiple Wallets', 'test2.csv',
                   named, FEE_POCKET)

    done = False
    next_target = 1
    while not done and test.balance('Wallet A', expected=True) >= MINIMUM_TRANSFER:
        count = 0
        while not done and count < CHECK_ITERATIONS:
            count += 1
            xfer, fee = pockets[0].session.separate_transfer_and_fee(
                test.balance('Wallet A', expected=True) *
                TRANSFER_PERCENTAGE_PER_STEP // 100)
            if xfer + fee < MINIMUM_TRANSFER:
                done = True
                break
            test.transfer('Wallet A', 'Wallet {}'.format(chr(ord('A') +
                                                             next_target)), xfer, fee)

            next_target += 1
            if next_target >= len(pockets):
                next_target = 1

        test.finish_transfers(check_balances=True)
    test.mark_successful()


def pk_test_3():
    INITIAL_AMOUNT_IN_COIN = 3141592
    INITIAL_AMOUNT = INITIAL_AMOUNT_IN_COIN * TOSHI_PER_COIN
    TRANSFER_PERCENTAGE_PER_STEP = 7
    CHECK_ITERATIONS = 10
    WALLET_COUNT = 11
    TARGET_INDEX = WALLET_COUNT - 1

    all_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE,
                                              WALLET_COUNT)
    pockets = [Pocket(walletlib.Session(None, wbytes, '')) for wbytes
               in all_walletbytes]
    named = {
        'Wallet {}'.format(chr(ord('A') + n)): pockets[n] for n in
        range(TARGET_INDEX)
    }
    named['Wallet X'] = pockets[TARGET_INDEX]

    for p in pockets[:TARGET_INDEX]:
        set_balance(p, INITIAL_AMOUNT)
    set_balance(pockets[TARGET_INDEX], 0)

    test = TestCSV('Test 3: Multiple Wallets to Single Wallet', 'test3.csv',
                   named, FEE_POCKET)

    done = False
    next_source = 0
    while not done and test.balance('Wallet A', expected=True) >= MINIMUM_TRANSFER:
        count = 0
        while not done and count < CHECK_ITERATIONS:
            count += 1
            xfer, fee = pockets[0].session.separate_transfer_and_fee(
                test.balance('Wallet {}'.format(chr(ord('A') + next_source),
                                                expected=True)) * TRANSFER_PERCENTAGE_PER_STEP // 100)
            if xfer + fee < MINIMUM_TRANSFER:
                done = True
                break
            test.transfer('Wallet {}'.format(chr(ord('A') + next_source)),
                          'Wallet X', xfer, fee)

            next_source += 1
            if next_source >= TARGET_INDEX:
                next_source = 0

        test.finish_transfers(check_balances=True)
    test.mark_successful()


def pk_test_4():
    DEBUG_MULTIPLIER = 1
    INITIAL_AMOUNT_WALLET_A_IN_COIN = 1_618_339
    INITIAL_AMOUNT_OTHERS_IN_COIN = 1_414_213
    STAGE1_TRANSFER_PER_STEP = 2_797_18_300 * DEBUG_MULTIPLIER  # in toshi
    STAGE1_STOP_IN_COIN = 1
    STAGE2_TRANSFER_PER_STEP = 7_377_37_000 * DEBUG_MULTIPLIER  # in toshi
    STAGE2_STOP_IN_COIN = 1
    BALANCE_CHECK_ROUNDS = 10
    WALLET_COUNT = 12

    all_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE,
                                              WALLET_COUNT)
    pockets = [Pocket(walletlib.Session(None, wbytes, '')) for wbytes
               in all_walletbytes]
    named = {
        'Wallet {}'.format(chr(ord('A') + n)): pockets[n] for n in
        range(len(pockets))
    }

    INITIAL_AMOUNT_WALLET_A = INITIAL_AMOUNT_WALLET_A_IN_COIN * TOSHI_PER_COIN
    INITIAL_AMOUNT_OTHERS = INITIAL_AMOUNT_OTHERS_IN_COIN * TOSHI_PER_COIN
    STAGE1_FEE_PER_STEP = pockets[0].session.calculate_transfer_fee(
        STAGE1_TRANSFER_PER_STEP)
    STAGE2_FEE_PER_STEP = pockets[0].session.calculate_transfer_fee(
        STAGE2_TRANSFER_PER_STEP)
    STAGE1_STOP = ((STAGE1_STOP_IN_COIN * TOSHI_PER_COIN) +
                   STAGE1_TRANSFER_PER_STEP + STAGE1_FEE_PER_STEP)
    STAGE2_STOP = ((STAGE2_STOP_IN_COIN * TOSHI_PER_COIN) +
                   STAGE2_TRANSFER_PER_STEP + STAGE2_FEE_PER_STEP)

    set_balances([
        (p, INITIAL_AMOUNT_WALLET_A) if n == 'Wallet A' else
        (p, 0) if n == 'Wallet B' else
        (p, INITIAL_AMOUNT_OTHERS)
        for n, p in named.items()
    ])

    # Set up the test.
    test = TestCSV('Test 4: Multiple Wallets to Multiple Wallets', 'test4.csv',
                   named, FEE_POCKET)

    # Transfer a specific amount from each of wallets C..L to wallet A until
    # they can't continue. At the same time, transfer a specific amount from
    # wallet A to wallet B, again until it can't continue.
    still_active = 1
    max_still_active = WALLET_COUNT - 1
    balance_check_rounds = 0
    next_source = 2
    tgt = 'Wallet A'
    while still_active:
        still_active = 0

        # Handle the transfers from the other wallets to wallet A.
        while True:
            src = 'Wallet {}'.format(chr(ord('A') + next_source))

            if test.balance(src, expected=True) > STAGE1_STOP:
                still_active += 1
                test.transfer(src, tgt, STAGE1_TRANSFER_PER_STEP,
                              STAGE1_FEE_PER_STEP)

            next_source += 1
            if next_source >= WALLET_COUNT:
                next_source = 2
                break

        # The second stage will run for a while after all of the first stage
        # transfers are complete, so once we've finished any first-stage ones,
        # we'll run more second-stage ones.
        current_run = 0
        max_runs = int(test.balance('Wallet A', expected=True) /
                       (STAGE2_TRANSFER_PER_STEP + STAGE2_FEE_PER_STEP))

        while still_active < max_still_active and current_run < max_runs:
            current_run += 1
            if test.balance('Wallet A', expected=True) >= STAGE2_STOP:
                still_active += 1
                test.transfer('Wallet A', 'Wallet B', STAGE2_TRANSFER_PER_STEP,
                              STAGE2_FEE_PER_STEP)
            else:
                break

        check = False
        balance_check_rounds += 1
        if balance_check_rounds >= BALANCE_CHECK_ROUNDS:
            if BALANCE_CHECK_ROUNDS != 0:
                check = True
            balance_check_rounds = 0

        test.finish_transfers(check_balances=check, raise_on_error=False)
    test.mark_successful()


def pk_test_5():
    TIMED_DELAY_SECONDS = 60
    # MAX_TEST_SECONDS = 5 * 24 * 60 * 60
    MAX_TEST_SECONDS = 60 * 60 * 24
    MINIMUM_ACCOUNT_VALUE = 1_00_000  # in toshi
    BALANCE_CHECK_ROUNDS = 5

    class WalletData:
        def __init__(self, name, initial_balance, pattern, *, timed=False):
            '''
            The 'pattern' is a list of three-item tuples, the numerator and
            denominator of the fraction of the current value that should be
            transferred in that step, and the wallet (by name) to transfer it
            to.

            A few of these have a denominator of zero, indicating that the
            first item is actually a fixed value rather than a ratio.

            When True, 'timed' indicates that all of the transfers out of that
            pocket should be done once every minute.
            '''
            self.name = name
            self.initial_balance = initial_balance
            self.pocket = None
            self.pattern = pattern
            self.timed = timed

            # Make sure all items in 'self.pattern' are three-item tuples, and
            # that the third one is a valid wallet name.
            for item in self.pattern:
                if len(item) != 3:
                    raise Exception("In WalletData for {}, item '{}' needs "
                                    "three parts.".format(self.name, item))
                ww = item[2].split()
                if (len(ww) != 2 or ww[0] != 'Wallet' or len(ww[1]) < 1
                        or len(ww[1]) > 2 or ww[1] != ww[1].upper()
                        or not ww[1].isalpha()
                        or (len(ww[1]) == 2 and (ww[1][0] != 'A' or ww[1][1] >
                                                 'C'))):
                    raise Exception("In WalletData for {}, item '{}' doesn't "
                                    "contain a valid target wallet.".format(self.name,
                                                                            item))

    print("Setting up wallets...")
    wallets = [  # max 184_467_440_737_095_51_615
        WalletData('Wallet A', 31_415_92_000,
                   [(22, 700, 'Wallet B'), (57, 6400, 'Wallet C'), (45, 6400, 'Wallet D'), (49, 6400, 'Wallet G'),
                    (22, 6400, 'Wallet AC')], timed=True),
        WalletData('Wallet B', 7_377_37_000, [(3, 100, 'Wallet A'), (7, 100, 'Wallet C')]),
        WalletData('Wallet C', 279_72_000, [(13, 100, 'Wallet D')]),
        WalletData('Wallet D', 1_666_66_000,
                   [(1, 1100, 'Wallet C'), (8, 900, 'Wallet B'), (1, 900, 'Wallet A'), (2, 900, 'Wallet E'),
                    (19, 1200, 'Wallet F'), (9, 2200, 'Wallet G'), (5, 1800, 'Wallet AC')], timed=True),
        WalletData('Wallet E', 6_674_000_67_000, [(17, 100, 'Wallet D'), (19, 4000, 'Wallet F')]),
        WalletData('Wallet F', 16_774_87_000, [(3, 6400, 'Wallet G')]),
        WalletData('Wallet G', 1_674_93_000,
                   [(2, 300, 'Wallet AC'), (4, 1100, 'Wallet A'), (3, 700, 'Wallet B'), (2, 300, 'Wallet C'),
                    (1, 600, 'Wallet D'), (4, 1100, 'Wallet E'), (3, 700, 'Wallet F')], timed=True),
        WalletData('Wallet H', 939_56_000, [(17, 100, 'Wallet A'), (19, 4000, 'Wallet I'), (3, 6400, 'Wallet O')]),
        WalletData('Wallet I', 9_109_383_56_000,
                   [(1, 1200, 'Wallet B'), (17, 100, 'Wallet J'), (19, 4000, 'Wallet P'), (13, 100, 'Wallet W'),
                    (66_66_000, 0, 'Wallet AC')]),
        WalletData('Wallet J', 18_998_400_34_000,
                   [(3, 6400, 'Wallet C'), (5, 1200, 'Wallet K'), (5, 900, 'Wallet Q'), (1, 6400, 'Wallet X'),
                    (1, 3200, 'Wallet AC')]),
        WalletData('Wallet K', 314_159_265_97_000,
                   [(166_66_000, 0, 'Wallet D'), (14_89_000, 0, 'Wallet L'), (5, 6400, 'Wallet R'),
                    (3, 3200, 'Wallet Y'), (7, 6400, 'Wallet AC')]),
        WalletData('Wallet L', 910_93_000,
                   [(9, 6400, 'Wallet E'), (7, 3200, 'Wallet M'), (11, 3200, 'Wallet S'), (63, 6400, 'Wallet Z'),
                    (61, 6400, 'Wallet AC')]),
        WalletData('Wallet M', 9_798_05_000,
                   [(29, 3200, 'Wallet F'), (13, 1600, 'Wallet N'), (49, 6400, 'Wallet T'), (19, 3200, 'Wallet AA'),
                    (27, 6400, 'Wallet AC')]),
        WalletData('Wallet N', 9_806_65_000,
                   [(57, 6400, 'Wallet G'), (53, 6400, 'Wallet U'), (17, 6400, 'Wallet AB'), (19, 6400, 'Wallet AC')]),
        WalletData('Wallet O', 9_785_48_000,
                   [(33, 6400, 'Wallet H'), (35, 6400, 'Wallet A'), (37, 6400, 'Wallet U'), (39, 6400, 'Wallet AC')]),
        WalletData('Wallet P', 167_262_19_000,
                   [(43, 1600, 'Wallet I'), (45, 6400, 'Wallet B'), (27, 6400, 'Wallet W'), (57, 6400, 'Wallet AC')]),
        WalletData('Wallet Q', 167_262_19_000,
                   [(53, 6400, 'Wallet J'), (17, 6400, 'Wallet C'), (19, 6400, 'Wallet R'), (33, 6400, 'Wallet X'),
                    (4, 900, 'Wallet AC')]),
        WalletData('Wallet R', 77_777_777_07_000,
                   [(5, 600, 'Wallet K'), (1, 1200, 'Wallet D'), (1167, 99000, 'Wallet Y'), (27, 6400, 'Wallet AC')]),
        WalletData('Wallet S', 93_956_541_37_000,
                   [(57, 6400, 'Wallet R'), (53, 6400, 'Wallet L'), (17, 6400, 'Wallet T'), (19, 6400, 'Wallet Z'),
                    (33, 6400, 'Wallet AC')]),
        WalletData('Wallet T', 88_888_888_88_000,
                   [(19, 4000, 'Wallet M'), (3, 6400, 'Wallet U'), (5, 1200, 'Wallet AA'), (5, 900, 'Wallet AC')]),
        WalletData('Wallet U', 271_871_871_87_000,
                   [(1, 6400, 'Wallet N'), (1, 3200, 'Wallet G'), (87, 800, 'Wallet B'), (31, 33300, 'Wallet AC')]),
        WalletData('Wallet V', 1_414_213_00_000,
                   [(5, 600, 'Wallet O'), (17, 500, 'Wallet H'), (19, 4000, 'Wallet A'), (3, 6400, 'Wallet W'),
                    (5, 1200, 'Wallet X'), (5, 900, 'Wallet Y'), (1, 6400, 'Wallet Z'), (1, 3200, 'Wallet AA'),
                    (823, 11000, 'Wallet AB'), (22, 700, 'Wallet AC'), (5, 700, 'Wallet N'), (37, 30000, 'Wallet G')]),
        WalletData('Wallet W', 13_170_000_00_000,
                   [(53, 6400, 'Wallet P'), (59, 6400, 'Wallet I'), (106, 900, 'Wallet B'), (168, 500, 'Wallet X'),
                    (23, 2000, 'Wallet Y'), (17, 100, 'Wallet Z'), (19, 4000, 'Wallet AA'), (3, 6400, 'Wallet AB'),
                    (5, 1200, 'Wallet AC')]),
        WalletData('Wallet X', 597_220_000_00_000,
                   [(271_80_000, 0, 'Wallet Q'), (1, 1200, 'Wallet J'), (17, 100, 'Wallet C'), (19, 4000, 'Wallet Y'),
                    (3, 6400, 'Wallet Z'), (5, 1200, 'Wallet AA'), (5, 900, 'Wallet AB'), (5, 900, 'Wallet AC')]),
        WalletData('Wallet Y', 198_847_000_00_000,
                   [(1, 1100, 'Wallet R'), (17, 100, 'Wallet K'), (19, 4000, 'Wallet D'), (3, 6400, 'Wallet Z'),
                    (5, 1200, 'Wallet AA'), (5, 900, 'Wallet AB'), (8, 900, 'Wallet AC')]),
        WalletData('Wallet Z', 57_240_000_37_000,
                   [(5, 900, 'Wallet S'), (1, 1100, 'Wallet L'), (17, 100, 'Wallet E'), (19, 4000, 'Wallet AA'),
                    (3, 6400, 'Wallet AB'), (5, 1200, 'Wallet AC')]),
        WalletData('Wallet AA', 597_360_000_97_000,
                   [(5, 900, 'Wallet T'), (166_66_000, 0, 'Wallet M'), (19, 4000, 'Wallet F'), (3, 6400, 'Wallet AB'),
                    (5, 1200, 'Wallet AC')]),
        WalletData('Wallet AB', 74_200_000_47_000,
                   [(167_49_000, 0, 'Wallet U'), (2, 300, 'Wallet N'), (7, 3300, 'Wallet G'), (5, 1200, 'Wallet AC')]),
        WalletData('Wallet AC', 6_673_00_000,
                   [(7, 600, 'Wallet A'), (7, 3300, 'Wallet B'), (5, 900, 'Wallet C'), (7, 3300, 'Wallet D'),
                    (8, 900, 'Wallet E'), (5, 1200, 'Wallet F'), (4, 900, 'Wallet G')], timed=True),
    ]

    print("Reading wallet bytes...")
    all_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE,
                                              len(wallets))

    print("Initializing sessions...")
    for n in range(len(wallets)):
        wallets[n].pocket = Pocket(walletlib.Session(None, all_walletbytes[n],
                                                     ''))

    # Get the current balances of the target wallets, and of the source wallet,
    # and make sure that we have enough coin to cover the test.
    print("Setting initial balances...")
    set_balances([(w.pocket, w.initial_balance) for w in wallets])
    print("Pockets have initial values.")

    # Set up the test and start running it.
    test = TestCSV('Test 5', 'test5.csv', {w.name: w.pocket for w in wallets},
                   FEE_POCKET)

    # Run it.
    balance_check_rounds = 0
    now = time.time()
    next_timed = now
    stop_test = now + MAX_TEST_SECONDS
    while True:
        print("Starting loop...")

        # This test will keep going until it times out.
        now = time.time()
        if now >= stop_test:
            break

        active_wallets = set()
        run_timed = False

        # Every so often we switch to the "timed" accounts, and run all of the
        # transfer for those.
        if now >= next_timed:
            run_timed = True
            next_timed = now + TIMED_DELAY_SECONDS

        for w in wallets:
            # We either run the timed transfers or the non-timed ones.
            if run_timed != w.timed:
                continue

            # Do all of the transfers in that wallet's pattern. Skip any that
            # would result in a balance of less than one coin in the source
            # pocket, or that are less than the minimum transfer value.
            src = w.name
            is_active = False
            for transfer in w.pattern:
                tgt = transfer[2]
                bal = test.balance(src, expected=True)
                if transfer[1] == 0:
                    amount = transfer[0]
                else:
                    amount = (bal * transfer[0] // transfer[1])

                if amount < MINIMUM_TRANSFER:
                    print("Skipping transfer of {} from {} to {} (min xfer)"
                          .format(as_coin(amount), src, tgt))
                    continue
                if bal - amount < MINIMUM_ACCOUNT_VALUE:
                    print("Skipping transfer of {} from {} to {} (min value)"
                          .format(as_coin(amount), src, tgt))
                    continue

                print("Starting transfer of {} from {} to {}".format(
                    as_coin(amount), src, tgt))
                test.transfer(src, tgt, amount)
                is_active = True

            if is_active:
                active_wallets.add(w)

                # We'll pause between active wallets, just to make sure we're
                # not overloading the servers.
                time.sleep(0.5)

        check = False
        balance_check_rounds += 1
        if balance_check_rounds >= BALANCE_CHECK_ROUNDS:
            if BALANCE_CHECK_ROUNDS != 0:
                check = True
            balance_check_rounds = 0

        test.finish_transfers(check_balances=check, raise_on_error=False)

    test.mark_successful()


def main():
    TEST = 5

    # Activate debugging code.
    walletlib.set_logging(sys.stderr.fileno())

    rval = True

    # Run one of the tests that PK requested.
    try:
        if TEST == 1:
            return pk_test_1()
        elif TEST == 2:
            return pk_test_2()
        elif TEST == 3:
            return pk_test_3()
        elif TEST == 4:
            return pk_test_4()
        elif TEST == 5:
            return pk_test_5()
        elif TEST != 0:
            print("TEST {} does not exist yet.".format(TEST))
            return False
    except Exception as e:
        print(traceback.format_exc())
        return False

    try:
        target_walletbytes = read_target_walletbytes(TARGET_WALLETBYTES_FILE, 8)

        # Create the source and target sessions. Immediately request their
        # balances to force the wallet library to create an internal session
        # for each; we won't check the results here.
        print("Initializing sessions...")
        sessions = []
        pockets = []
        idx = 0
        for b in [SOURCE_WALLETBYTES] + target_walletbytes:
            print("{}... ".format(idx), end='')
            sys.stdout.flush()
            idx += 1

            w = walletlib.Session(None, b, '')
            assert w.get_address(0, walletlib.TESTNET) is not None, "Bad wallet bytes."
            sessions.append(w)

            p = Pocket(w)
            pockets.append(p)
            p.balance_request_start()
        for p in pockets:
            p.balance_request_finish()

        print("done")

        # for s in sessions:
        #    print("getFoundationBytes: {}".format(s.get_foundation_bytes()))
        # return True  # TODO

        # Do a number of rounds of testing.
        for rnum in range(10):
            if not run_multitransfer_test_round(rnum, Pocket(sessions[0]), [
                Pocket(s) for s in sessions[1:]]):
                rval = False
                break
    except Exception as e:
        print(traceback.format_exc())
        rval = False
    finally:
        # Close each of the sessions.
        print("\nClosing sessions...")
        for s in range(len(sessions)):
            print("{}... ".format(s), end='')
            sys.stdout.flush()
            sessions[s].close()
        print("done")

        return rval


if __name__ == "__main__":
    try:
        sys.exit(0 if main() else 1)
    except Exception as e:
        print("Exception: {}".format(e))
        sys.exit(1)
