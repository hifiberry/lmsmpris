'''
Copyright (c) 2018 Modul 9/HiFiBerry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import logging
import threading
import socket


def lms_decode(s):

    res = ""

    it = iter(range(len(s)))
    for i in it:
        if s[i] != '%':
            res = res + s[i]
        else:
            code = "0x" + s[i + 1:i + 3]
            asciicode = int(code, 16)
            res = res + chr(asciicode)
            next(it)
            next(it)

    return res


def response_to_dict(parts):
    if parts is None:
        return {}

    res = {}
    for part in parts:
        part = lms_decode(part)
        if ":" in part:
            [tag, content] = part.split(":", 1)
            res[tag] = content
    return res


def local_networks():
    """
    Return my IPs. Needed to check if this client is connected to
    a specific LMS
    """
    from netifaces import interfaces, ifaddresses, AF_INET
    netlist = []
    for interface in interfaces():
        addrs = ifaddresses(interface)
        if AF_INET in addrs:
            netlist.extend(addrs[AF_INET])
    return netlist


def my_ips():
    res = []
    for net in local_networks():
        if not net["addr"].startswith("127."):
            res.append(net["addr"])
    return res


def broadcast(ip):
    for net in local_networks():
        if net["addr"] == ip:
            return net["broadcast"]


class LMSDiscoverer():
    """
    derived from https://pastebin.com/5jfta04x
    """

    DISCOVERY_PORT = 3483
    TIMEOUT_MS = 2500
    DISCOVERY_PACKET = b"eIPAD\0NAME\0JSON\0VERS\0"

    # Translate LMS's TLV tag names to easier to read names
    DISCOVERY_TAGS = {
        'NAME': 'name',
        'IPAD': 'host',
        'JSON': 'http_port',
        'VERS': 'version',
        'UUID': 'uuid',
    }

    def __init__(self):
        pass

    def discover_all(self):
        # Use a dict keyed by server IP address to deduplicate servers
        servers = {}
        for ip in my_ips():
            servers.update(self.discover(ip))

        servers = [server for _ip,server in servers.items()]

        return servers

    def discover(self, source_address):
        servers = {}

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP
        client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        client.bind((source_address, 0))
        client.settimeout(1)
        logging.debug("sending discovery packet %s",
                      LMSDiscoverer.DISCOVERY_PACKET)
        try:
            client.sendto(LMSDiscoverer.DISCOVERY_PACKET,
                          ('<broadcast>', LMSDiscoverer.DISCOVERY_PORT))
        except OSError:
            # The interface might not support broadcasts
            return []

        while True:
            try:
                data, (ip, _port) = client.recvfrom(1024)
                logging.debug("received message from %s: %s", ip, data)

                # Parse discovery response, based on LMS perl implementation:
                # https://github.com/LMS-Community/slimserver/blob/8.5.1/Slim/Networking/Discovery/Server.pm#L182
                # https://github.com/LMS-Community/slimserver/blob/8.5.1/Slim/Networking/Discovery.pm#L153
                msg = data.decode()
                if msg[0] == 'E':
                    msg = msg[1:] # drop leading E

                    server = {}
                    remaining = len(msg)
                    while remaining > 0:
                        tag = msg[0:4]
                        length = ord(msg[4])
                        val = msg[5:5+length] if length > 0 else None
                        if val:
                            server[LMSDiscoverer.DISCOVERY_TAGS[tag]] = val
                        msg = msg[5+length:]
                        remaining = remaining - 5 - length

                    if server:
                        # We've parsed a useful response...
                        if "host" not in server:
                            # ...but didn't get an IP address
                            # (LMS only returns the IP address if it's explicitly set by the server
                            # admin, otherwise we're supposed to use the discovery packet IP)
                            server["host"] = ip
                        servers[ip] = server
                        logging.debug("Parsed server discovery response: %s", server)

            except socket.timeout:
                break

        logging.debug("closing socket")
        client.close()

        return servers

    def discover_my_lms(self):
        servers = self.discover_all()
        if servers == []:
            logging.warning("could not discover any Logitech Media servers")
            return

        ips = my_ips()
        result = None
        for server in servers:
            lms = LMS(**server)
            lms.connect()
            me = lms.client(ips)
            lms.disconnect()
            if me is not None:
                # looks like, this system is connected to this LMS
                result = server
                break

        return result


class StatusDisplay():

    def __init__(self):
        pass

    def notify_status(self, mac, statusdict):
        logging.info("%s: %s", mac, statusdict)

    def notify_line(self, parts):
        pass


class CommandResponseListener(threading.Thread):

    def __init__(self, lmslistener, cmdline, timeout):
        threading.Thread.__init__(self)
        self.lmslistener = lmslistener
        self.cmdline = cmdline
        self.parts = cmdline.split(" ")
        self.timeout = timeout
        self.lock = threading.Lock()
        self.result = None

    def notify_line(self, parts):

        # Check if this is a reponse to the given command
        if len(parts) < len(self.parts):
            return

        # answer should start with the full command string
        for i in range(0, len(self.parts)):
            if self.parts[i] != parts[i]:
                return

        # Ok, this is an answer to our request, store it and release

        self.result = parts
        self.lock.release()

    def run(self):
        if not(self.lock.acquire(self.timeout)):
            logging.info("timeout waitung for response to %s",
                         self.commandline)

    def read_response(self):
        if not(self.lock.acquire(blocking=False)):
            logging.error("internal error: can't get lock")

        self.lmslistener.add_line_listener(self)
        self.start()
        self.lmslistener.send(self.cmdline)
        self.join()
        self.lmslistener.remove_line_listener(self)
        return self.result


class LMS():

    def __init__(self, host=None, port=9090, http_port=9000, find_my_server=False, **kwargs):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.find_my_server = find_my_server
        self.socket = None
        self.status_listeners = []
        self.line_listeners = []

    def connect(self):
        """
        - find LMS server
        - check if the
        - connect to server (port 9090)
        - subscribe to status updates
        """

        if self.host is None:
            my_lms = None
            discover = LMSDiscoverer()

            if self.find_my_server:
                try:
                    my_lms = discover.discover_my_lms()
                except Exception as e:
                    logging.info("Couldn't connect to LMS: %s", e)

            else:
                # select the first LMS server that we can find
                servers = discover.discover_all()
                if servers != []:
                    my_lms = servers[0]

            logging.debug("Using LMS: %s", my_lms)

            if my_lms is None:
                logging.debug("Could not find any LMS to use")
                raise IOError("No LMS host to connect to.")
            else:
                self.host = my_lms["host"]
                self.port = my_lms.get("port", self.port)
                self.http_port = my_lms.get("http_port", self.http_port)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.host, self.port))
        self.socket = sock
        reader = threading.Thread(target=self.listen)
        reader.start()

    def disconnect(self):
        logging.debug("disconnecting from server")
        socket = self.socket
        self.socket = None
        socket.close()

    def add_status_listener(self, listener):
        self.status_listeners.append(listener)

    def remove_status_listener(self, listener):
        self.status_listeners.remove(listener)

    def add_line_listener(self, listener):
        self.line_listeners.append(listener)

    def remove_line_listener(self, listener):
        self.line_listeners.remove(listener)

    def send(self, command):
        """
        Command examples:

        Play/Pause...
        b8%3A27%3Aeb%3Ac3%3Aa3%3Aae play/pause

        Next/Prev
        b8%3A27%3Aeb%3Ac3%3Aa3%3Aae playlist index +1
        b8%3A27%3Aeb%3Ac3%3Aa3%3Aae playlist index -1

        Subscribe to status updates:
        b8%3A27%3Aeb%3Ac3%3Aa3%3Aae status - 1 tags%3AadKlj subscribe 2

        special characters in strings needs HTML encoding,
        e.g. %3A for :
        """
        if self.socket is None:
            logging.warn("LMS socket not connected, ignoring command")
            return

        if not(command.endswith("\n")):
            commandline = command + "\n"

        self.socket.sendall(commandline.encode())
        logging.debug("sent %s", command)

    def cmd_response(self, command, timeout=10):
        return CommandResponseListener(self, command, timeout).read_response()

    def listen(self):
        if self.socket is None:
            logging.warn("LMS socket not connected")
            return

        buffer = ""
        try:
            while 1:
                if "\n" in buffer:
                    line = buffer
                else:
                    data = self.socket.recv(1024)
                    if not data:
                        break
                    line = buffer + data.decode()

                buffer = ""

                lf = line.find("\n")
                if lf == -1:
                    # Got no full line, waiting for more data
                    buffer = line
                elif lf < len(line) - 1:
                    buffer = line[lf + 1:]
                    line = line[:lf]
                else:
                    line = line[:-1]

                parts = []
                status = {}
                i = 0
                is_status = False
                for p in line.split(" "):
                    part = lms_decode(p)
                    if i == 1 and p == "status":
                        is_status = True
                    i += 1

                    # automatically split status into a dict
                    if is_status and ":" in part:
                        [tag, content] = part.split(":", 1)
                        status[tag] = content

                    parts.append(part)

                if is_status:
                    for listener in self.status_listeners:
                        listener.notify_status(parts[0], status)

                for listener in self.line_listeners:
                    listener.notify_line(parts)

                logging.debug("got %s from LMS", line)

        except IOError as e:
            if self.socket is not None:
                logging.warn("I/O error, connection probably closed, %s",
                              e)

        self.socket = None

    def is_connected(self):
        return self.socket is not None

    def players(self):
        index = 0
        res = []
        while True:
            cmd = "players {} 1".format(index)
            resp = self.cmd_response(cmd)
            response = response_to_dict(resp)
            if "playerindex" in response:
                res.append(response)
            else:
                return res
            index += 1

    def client(self, iplist=None):
        if iplist is None:
            iplist = my_ips()

        logging.info("My IPs: %s",iplist)

        for player in self.players():
            [ip, _port] = player["ip"].split(":", 1)
            logging.info("Client: %s",ip) 
            if ip in iplist:
                return player

    def cover_url(self, artwork_track_id):
        return "http://{}:{}/music/{}/cover.jpg".format(self.host,
                                                        self.http_port,
                                                        artwork_track_id)

    def __str__(self):
        if self.is_connected():
            return "LMS/{}/connected".format(self.host)
        else:
            return "LMS/{}/not connected".format(self.host)


if __name__ == "__main__":
    lms = LMS(find_my_server=True)
    lms.connect()
    print(lms)
