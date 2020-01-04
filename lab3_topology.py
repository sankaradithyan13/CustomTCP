#!/usr/bin/python

import sys
import os
import atexit
import argparse
import textwrap

from mininext.topo import Topo
from mininext.services.quagga import QuaggaService

# patch isShellBuiltin
import mininet.util
import mininext.util
mininet.util.isShellBuiltin = mininext.util.isShellBuiltin
sys.modules['mininet.util'] = mininet.util

from mininet.util import dumpNodeConnections
from mininet.node import OVSController, RemoteController
from mininet.log import setLogLevel, info

from mininext.cli import CLI
from mininext.net import MiniNExT
from mininet.link import Link, TCLink

from subprocess import check_call

sys.path.append(os.getcwd())

net = None

class dumbbell(Topo):
    """
    Create a dumbell topology with 2 clients, 2 servers and a constrained link
    between two routers in the middle. The nodes are named client1, client2,
    router1, router2, and server1 and server2.
    """
    def __init__(self):
        """Initialize a Quagga topology based on the topology information"""
        Topo.__init__(self)

        info("*** Creating dumbbell topology and adding nodes")
        self.addHost(name="client1", hostname="client1", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)
        self.addHost(name="client2", hostname="client2", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)
        self.addHost(name="router1", hostname="router1", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)
        self.addHost(name="router2", hostname="router2", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)
        self.addHost(name="server1", hostname="server1", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)
        self.addHost(name="server2", hostname="server2", privateLogDir=True, privateRunDir=True, inMountNamespace=True, inPIDNamespace=True, inUTSNamespace=True)

def config_dumbbell(topo):
    global net
    net = MiniNExT(topo, controller=OVSController, link=TCLink)
    info("*** Creating links between nodes in dumbbell topology\n")
    net.addLink(net.getNodeByName("client1"), net.getNodeByName("router1"), intfName1="meth1", intfName2="client1")
    net.addLink(net.getNodeByName("client2"), net.getNodeByName("router1"), intfName1="meth1", intfName2="client2")
    net.addLink(net.getNodeByName("router1"), net.getNodeByName("router2"), intfName1="router2", intfName2="router1", bw=1)
    net.addLink(net.getNodeByName("server1"), net.getNodeByName("router2"), intfName1="meth1", intfName2="server1")
    net.addLink(net.getNodeByName("server2"), net.getNodeByName("router2"), intfName1="meth1", intfName2="server2")

    info("*** Configuring IP addresses and routing tables in dumbbell topology\n")
    client1 = net.getNodeByName("client1")
    client1.cmd("ifconfig meth1 %s/24 up" % ("10.0.1.1"))
    client1.cmd("route add default gw %s meth1" % ("10.0.1.2"))
    client1.cmd("iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP")

    client2 = net.getNodeByName("client2")
    client2.cmd("ifconfig meth1 %s/24 up" % ("10.0.2.1"))
    client2.cmd("route add default gw %s meth1" % ("10.0.2.2"))
    client2.cmd("iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP")

    server1 = net.getNodeByName("server1")
    server1.cmd("ifconfig meth1 %s/24 up" % ("12.0.1.1"))
    server1.cmd("route add default gw %s meth1" % ("12.0.1.2"))
    server1.cmd("iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP")

    server2 = net.getNodeByName("server2")
    server2.cmd("ifconfig meth1 %s/24 up" % ("12.0.2.1"))
    server2.cmd("route add default gw %s meth1" % ("12.0.2.2"))
    server2.cmd("iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP")

    router1 = net.getNodeByName("router1")
    router1.cmd("ifconfig client1 %s/24 up" % ("10.0.1.2"))
    router1.cmd("ifconfig client2 %s/24 up" % ("10.0.2.2"))
    router1.cmd("ifconfig router2 %s/24 up" % ("11.0.1.1"))
    router1.cmd("route add -net %s/8 gw %s dev router2" % ("12.0.0.0", "11.0.1.2"))
    router1.cmd("route add -net %s/24 gw %s dev client1" % ("10.0.1.0", "10.0.1.1"))
    router1.cmd("route add -net %s/24 gw %s dev client2" % ("10.0.2.0", "10.0.2.1"))

    router2 = net.getNodeByName("router2")
    router2.cmd("ifconfig server1 %s/24 up" % ("12.0.1.2"))
    router2.cmd("ifconfig server2 %s/24 up" % ("12.0.2.2"))
    router2.cmd("ifconfig router1 %s/24 up" % ("11.0.1.2"))
    router2.cmd("route add -net %s/8 gw %s dev router1" % ("10.0.0.0", "11.0.1.1"))
    router2.cmd("route add -net %s/24 gw %s dev server1" % ("12.0.1.0", "12.0.1.1"))
    router2.cmd("route add -net %s/24 gw %s dev server2" % ("12.0.2.0", "12.0.2.1"))

    return net

def dumbbell_topology():
    setLogLevel('info')
    topo = dumbbell()
    return config_dumbbell(topo)

if __name__ == '__main__':
    setLogLevel( 'info' )
    topo = dumbbell()
    net = config_dumbbell(topo)
    CLI(net)
    net.stop()
