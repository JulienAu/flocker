# Copyright ClusterHQ Inc.  See LICENSE file for details.
# -*- test-case-name: flocker.control.test.test_script -*-

"""
Script for starting control service server.
"""

import cProfile
import signal
from functools import partial
from time import clock

from twisted.python.usage import Options
from twisted.internet.endpoints import serverFromString
from twisted.internet.defer import maybeDeferred
from twisted.python.filepath import FilePath
from twisted.application.service import MultiService
from twisted.internet.ssl import Certificate

from .httpapi import create_api_service, REST_API_PORT
from ._persistence import (
    ConfigurationPersistenceService,
    FilePathConfigurationStore,
)
from ._clusterstate import ClusterStateService
from ..common.script import (
    flocker_standard_options, FlockerScriptRunner, main_for_service,
    enable_profiling, disable_profiling)
from ._protocol import ControlAMPService
from ..ca import (
    rest_api_context_factory, ControlCredential, amp_server_context_factory,
)

DEFAULT_CERTIFICATE_PATH = b"/etc/flocker"


@flocker_standard_options
class ControlOptions(Options):
    """
    Command line options for ``flocker-control`` cluster management process.
    """
    optParameters = [
        ["data-path", "d", FilePath(b"/var/lib/flocker"),
         "The directory where data will be persisted.", FilePath],
        ["port", "p", 'tcp:%d' % (REST_API_PORT,),
         "The external API port to listen on."],
        ["agent-port", "a", 'tcp:4524',
         "The port convergence agents will connect to."],
        ["certificates-directory", "c", DEFAULT_CERTIFICATE_PATH,
         ("Absolute path to directory containing the cluster "
          "root certificate (cluster.crt) and control service certificate "
          "and private key (control-service.crt and control-service.key).")],
    ]


class ControlScript(object):
    """
    A command to start a long-running process to control a Flocker
    cluster.
    """
    def main(self, reactor, options):
        certificates_path = FilePath(options["certificates-directory"])
        ca = Certificate.loadPEM(
            certificates_path.child(b"cluster.crt").getContent())
        # This is a hack; from_path should be more
        # flexible. https://clusterhq.atlassian.net/browse/FLOC-1865
        control_credential = ControlCredential.from_path(
            certificates_path, b"service")

        d = maybeDeferred(
            FilePathConfigurationStore.from_directory,
            options["data-path"],
        )

        def make_persistence_service(configuration_store):
            return ConfigurationPersistenceService.from_configuration_store(
                reactor,
                configuration_store
            )
        d.addCallback(make_persistence_service)

        def start_services(persistence_service):
            top_service = MultiService()
            persistence_service.setServiceParent(top_service)
            cluster_state = ClusterStateService(reactor)
            cluster_state.setServiceParent(top_service)
            api_service = create_api_service(
                persistence_service, cluster_state, serverFromString(
                    reactor, options["port"]),
                rest_api_context_factory(ca, control_credential))
            api_service.setServiceParent(top_service)
            amp_service = ControlAMPService(
                reactor, cluster_state, persistence_service, serverFromString(
                    reactor, options["agent-port"]),
                amp_server_context_factory(ca, control_credential))
            amp_service.setServiceParent(top_service)
            return main_for_service(reactor, top_service)
        d.addCallback(start_services)
        return d


def flocker_control_main():
    # Use CPU time instead of wallclock time.
    # The control service does a lot of waiting and we do not
    # want the profiler to include that.
    pr = cProfile.Profile(clock)

    signal.signal(signal.SIGUSR1, partial(enable_profiling, pr))
    signal.signal(signal.SIGUSR2, partial(disable_profiling, pr, 'control'))

    return FlockerScriptRunner(
        script=ControlScript(),
        options=ControlOptions()
    ).main()
