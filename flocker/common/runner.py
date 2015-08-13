# -*- test-case-name: admin.test.test_runner -*-
# Copyright Hybrid Logic Ltd.  See LICENSE file for details.
# -*- test-case-name: flocker.common.test.test_runner -*-
"""
Tools for running commands.
"""
import os
from pipes import quote as shell_quote
from collections import defaultdict

from characteristic import attributes
from eliot import MessageType, ActionType, Field
from eliot.twisted import DeferredContext

from twisted.internet.error import ProcessDone
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ProcessProtocol

from twisted.protocols.basic import LineOnlyReceiver


RUN_ACTION = ActionType(
    action_type="flocker.common.runner:run",
    startFields=[
        Field.for_types(u"command", [list], u"The command.")
    ],
    successFields=[],
    description="Run a command.",
)
RUN_OUTPUT_MESSAGE = MessageType(
    message_type="flocker.common.runner:run:output",
    fields=[
        Field.for_types(u"line", [bytes], u"The output."),
    ],
    description=u"A line of command output.",
)


# LineOnlyReceiver is mutable, so can't use pyrsistent
@attributes([
    "handle_line",
])
class _LineParser(LineOnlyReceiver, object):
    """
    Parser that breaks input into lines, and writes it to ouput.

    :ivar handle_line: Callable to call with parsed lines.
    """
    delimiter = b'\n'

    def __init__(self):
        self.transport = type('', (object,), {})()
        self.transport.disconnecting = False

    def lineReceived(self, line):
        self.handle_line(line)


@attributes([
    "deferred",
    "handle_line",
])
class CommandProtocol(ProcessProtocol, object):
    """
    Protocol that logs the lines of a remote command.

    :ivar Deferred deferred: Deferred to fire when the command finishes
        If the command finished successfully, will fire with ``None``.
        Otherwise, errbacks with the reason.
    :ivar handle_line: Callable to call with parsed lines.

    :ivar defaultdict _fds: Mapping from file descriptors to `_LineParsers`.
    """
    def __init__(self):
        self._fds = defaultdict(
            lambda: _LineParser(handle_line=self.handle_line))

    def childDataReceived(self, childFD, data):
        self._fds[childFD].dataReceived(data)

    def processEnded(self, reason):
        if reason.check(ProcessDone):
            self.deferred.callback(None)
        else:
            self.deferred.errback(reason)


def run(reactor, command, handle_line=None, **kwargs):
    """
    Run a process and kill it if the reactor stops.

    :param reactor: Reactor to use.
    :param list command: The command to run.
    :param handle_line: Callable that will be called with lines parsed
        from the command output. By default logs an Eliot message.

    :return Deferred: Deferred that fires when the process is ended.
    """
    if 'env' not in kwargs:
        kwargs['env'] = os.environ

    action = RUN_ACTION(command=command)

    if handle_line is None:
        def handle_line(line):
            RUN_OUTPUT_MESSAGE(
                line=line,
            ).write(action=action)

    protocol_done = Deferred()
    protocol = CommandProtocol(deferred=protocol_done, handle_line=handle_line)

    with action.context():
        protocol_done = DeferredContext(protocol_done)
        reactor.spawnProcess(protocol, command[0], command, **kwargs)

        def unregister_killer(result, trigger_id):
            try:
                reactor.removeSystemEventTrigger(trigger_id)
            except:
                # If we can't remove the trigger, presumably it has already
                # been removed (or run). In any case, there is nothing sensible
                # to do if this fails.
                pass
            return result
        trigger_id = reactor.addSystemEventTrigger(
            'before', 'shutdown', protocol.transport.signalProcess, 'TERM')
        protocol_done.addBoth(unregister_killer, trigger_id)

        return protocol_done.addActionFinish()

SSH_OPTIONS = [
    b"-C",  # compress traffic
    b"-q",  # suppress warnings
    # We're ok with unknown hosts.
    b"-o", b"StrictHostKeyChecking=no",
    # The tests hang if ControlMaster is set, since OpenSSH won't
    # ever close the connection to the test server.
    b"-o", b"ControlMaster=no",
    # Some systems (notably Ubuntu) enable GSSAPI authentication which
    # involves a slow DNS operation before failing and moving on to a
    # working mechanism.  The expectation is that key-based auth will
    # be in use so just jump straight to that.
    b"-o", b"PreferredAuthentications=publickey"
]


def run_ssh(reactor, username, host, command, handle_line=None, **kwargs):
    """
    Run a process on a remote server using the locally installed ``ssh``
    command and kill it if the reactor stops.

    :param reactor: Reactor to use.
    :param username: The username to use when logging into the remote server.
    :param host: The hostname or IP address of the remote server.
    :param list command: The command to run remotely.
    :param handle_line: Callable that will be called with lines parsed
        from the command output. By default logs an Eliot message.

    :return Deferred: Deferred that fires when the process is ended.
    """
    ssh_command = [
        b"ssh",
    ] + SSH_OPTIONS + [
        b"-l", username,
        host,
        ' '.join(map(shell_quote, command)),
    ]

    return run(
        reactor,
        username,
        host,
        ssh_command,
        handle_line=handle_line,
        **kwargs
    )


def download_file(reactor, username, host, remote_path, local_path):
    """
    Run the local ``scp`` command to download a single file from a remote
    host and kill it if the reactor stops.

    :param reactor: Reactor to use.
    :param username: The username to use when logging into the remote server.
    :param host: The hostname or IP address of the remote server.
    :param FilePath remote_path: The path of the file on the remote host.
    :param FilePath local_path: The path of the file on the local host.
    :return Deferred: Deferred that fires when the process is ended.
    """
    scp_command = [
        b"scp",
    ] + SSH_OPTIONS + [
        username + b'@' + host + b':' + remote_path.path,
        local_path.path
    ]

    return run(
        reactor,
        username,
        host,
        scp_command,
    )
