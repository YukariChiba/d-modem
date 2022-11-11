import subprocess
import threading
import select
import time
import tty
import pathlib
import re
import os
import signal
import ipaddress
import io
import pty
import logging
import pwd
import ctypes
import argparse
from typing import Union, Dict, List, Tuple, Callable, Any

logging.basicConfig(level=logging.DEBUG,format='%(threadName)-5s - %(levelname)s - %(message)s')
logger = logging.getLogger()

PJSIP_V6 = False
# exports PJSIP_IPV6=1 to d-modem
# uses ipv6 sip transport to preserve address space
# utilize ipv4 over v6 next hop so that no v4 is required in the container

PTY_LOC = pathlib.Path("/tmp")
RUN_AS = "nobody"
get_pty = lambda n: PTY_LOC / f"ttySL{n}"

MODEMD = "./slmodemd/slmodemd"
D_MODEM = './d-modem.nopulse'

IP4RANGE = ipaddress.ip_network("10.0.0.0/27")
IP6RANGE = ipaddress.ip_network("fd00::/64")

PRODUCTION = False

# Do we really need that many?
# modem_configs: Dict[int, List[str]] = {
#     **{i: (["AT+MS=92,1" ], lambda: PPPProc) for i in range(00, 05)},
#     **{i: (["AT+MS=90,1" ], lambda: PPPProc) for i in range(05, 10)},
#     **{i: (["AT+MS=34,1" ], lambda: PPPProc) for i in range(10, 15)},
#     **{i: (["AT+MS=132,1"], lambda: PPPProc) for i in range(15, 20)},
#     **{i: (["AT+MS=32,1" ], lambda: PPPProc) for i in range(20, 25)}, # broken?
#     **{i: (["AT+MS=122,1"], lambda: PPPProc) for i in range(25, 30)},
#     **{i: (["AT+MS=22,1" ], lambda: PPPProc) for i in range(30, 35)},
#     **{i: (["AT+MS=212,1"], lambda: PPPProc) for i in range(35, 40)},
#     **{i: (["AT+MS=23,1" ], lambda: PPPProc) for i in range(40, 45)},
#     **{i: (["AT+MS=21,1" ], lambda: PPPProc) for i in range(45, 50)}, # broken?
#     **{i: (["AT+MS=103,1"], lambda: PPPProc) for i in range(50, 55)},
# }

modem_configs: Dict[int, Tuple[List[str], Callable]] = {
    **{i: (["AT+MS=90,1"], lambda: PPPProc) for i in range(0, 1)},
    **{i: (["AT+MS=34,1"], lambda: PPPProc) for i in range(1, 2)},
    **{i: (["AT+MS=32,1"], lambda: PPPProc) for i in range(2, 3)},
    **{i: (["AT+MS=22,1"], lambda: PPPProc) for i in range(3, 5)},
    **{i: (["AT+MS=23,1"], lambda: PPPProc) for i in range(5, 7)},
}

assert all(i >= 0 for i in modem_configs)

class BaseProc:
    def __init__(self) -> None:
        self.proc: subprocess.Popen = None
    def proc(self) -> subprocess.Popen:
        return self.proc
    def terminate(self, *args, **kwargs):
        return self.proc.terminate(*args, **kwargs)
    def wait(self, *args, **kwargs):
        return self.proc.wait(*args, **kwargs)
    def poll(self, *args, **kwargs):
        return self.proc.poll(*args, **kwargs)
    def send_signal(self, *args, **kwargs):
        return self.proc.send_signal(*args, **kwargs)

class ShellProc(BaseProc):
    ''' danger! '''
    def __init__(self, ptyr: io.BufferedReader, ptyw: io.BufferedWriter, speed: int, no: int,
                pty_path: pathlib.Path, ip: Tuple[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> None:
        assert not PRODUCTION
        self.proc = subprocess.Popen(
            ['bash', '-c', 'stty sane;exec bash'],
            stdin=ptyr,
            stdout=ptyw,
            stderr=ptyw,
            preexec_fn=os.setsid,
            env=dict(os.environ, TERM='vt100')
        )

class PPPProc(BaseProc):
    def __init__(self, ptyr: io.BufferedReader, ptyw: io.BufferedWriter, speed: int, no: int,
                pty_path: pathlib.Path, ip: Tuple[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> None:
        self.no = no
        self.ip = ip
        ptyw.write((
            f"Welcome to D-Modem\r\nExample pppd launch options:\r\n"
            f" pppd /dev/ttyACM0 {speed} defaultroute persist noproxyarp noauth modem nodetach\r\n"
            f"Your ipv6 address is {ip[1]}.\r\n"
            f"Please add Address={ip[1]}/{IP6RANGE.prefixlen} Gateway={IP6RANGE[1]}\r\n"
             "to your ppp network device.\r\n"
             "Enjoy!\r\n\r\n\r\n"
        ).encode('utf-8'))
        ll_ident = lambda x: ipaddress.IPv6Address(int(x) & int(ipaddress.IPv6Address(2**(128-IP6RANGE.prefixlen)-1)))
        self.proc = PopenComm(
            [
                'pppd', str(pty_path.resolve()), str(speed), 'noproxyarp', 'passive', 'noauth',
                'unit', str(no), 'modem', 'nodefaultroute', 'nodetach', f"{IP4RANGE[0]}:{ip[0]}",
                '+ipv6', 'ipv6', f"{ll_ident(IP6RANGE[1])},{ll_ident(ip[1])}",
                'ipcp-max-configure', '100', 'ipcp-max-failure', '100', 'ipcp-restart', '15',
                'ipv6cp-max-configure', '100', 'ipv6cp-max-failure', '100', 'ipv6cp-restart', '15'
            ]
        )
    def wait(self, *args, **kwargs):
        while (lines := self.proc.readline()) is not None:
            for line in lines:
                logger.info(f"pppd: {line}")
                if 'remote LL address' in line:
                    try:
                        subprocess.run(
                            [
                                'ip', '-6', 'a', 'replace', 'dev', f"ppp{self.no}",
                                f"{IP6RANGE[1]}/128", 'peer', f"{self.ip[1]}/128"
                            ],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        logger.info(f"added v6 route to ppp{self.no}")
                    except Exception:
                        logger.exception(f"adding v6 route to ppp{self.no}")
        super().wait(*args, **kwargs)

class ModemManagerError(Exception):
    pass
class ReadLineException(ModemManagerError):
    pass
class PPPDDead(ModemManagerError):
    pass
class CarrierLost(ModemManagerError):
    pass
class ModemManager:
    terminating = False
    ''' lol not that modem_manager '''
    def __init__(self):
        self.modems = {no: Modem(no, config) for no, config in modem_configs.items()}
        self.ip_used4 = {IP4RANGE[0]}
        self.ip_used6 = {IP6RANGE[0], IP6RANGE[1]}

class PopenComm(subprocess.Popen):
    def __init__(self, *args, **kwargs) -> None:
        try:
            self._popen_comm_master, self._popen_comm_slave = pty.openpty()
            os.set_blocking(self._popen_comm_master, False)
            self._popen_comm_r = open(self._popen_comm_master, "rb", buffering=0)
            kwargs['stdin'] = kwargs['stdout'] = kwargs['stderr'] = self._popen_comm_master
            super().__init__(*args, **kwargs)
        except Exception:
            self._popen_comm_cleanup()
            raise
        def w(self):
            try:
                super().wait(timeout=None)
            except Exception:
                logger.exception("Popen wait")
            self._popen_comm_cleanup()
        self.wtr = threading.Thread(target=w, args=(self,))
        self.wtr.start()

    def wait(self, timeout=None) -> int:
        if timeout is not None:
            return super().wait(timeout=timeout)
        self.wtr.join()
        if self.poll() is None:
            return super().wait(timeout=None)
        return self.returncode

    def _popen_comm_cleanup(self) -> None:
        try:
            os.close(self._popen_comm_slave)
        except Exception:
            pass
        try:
            self._popen_comm_r.close()
        except Exception:
            pass
        try:
            os.close(self._popen_comm_master)
        except Exception:
            pass
        logger.info(f"{self} pty pair closed")

    def readline(self) -> Union[List[str], None]:
        ''' blocks '''
        try:
            select.select([self._popen_comm_master], list(), list())
            read = self._popen_comm_r.read().decode('utf-8', errors='replace')
        except (OSError, ValueError):
            return None
        except Exception:
            logger.exception("readline")
            return None
        if not read:
            return None
        got = read.split('\n')
        if not got[-1]:
            got.pop(-1)
        return got

class IP_Request:
    lock = {4: threading.Lock(), 6: threading.Lock()}
    def __init__(self, version: int) -> None:
        assert version in {4, 6}
        self.v = version
        self.addr = None
        self.used = modem_manger.ip_used4 if self.v == 4 else modem_manger.ip_used6
    def __enter__(self):
        with self.lock[self.v]:
            for address in (IP4RANGE if self.v == 4 else IP6RANGE):
                if address in self.used:
                    continue
                else:
                    self.addr = address
                    self.used.add(address)
                    logger.info(f"ipv{self.v} lease: {str(self.addr)}")
                    return address
        raise RuntimeError(f"error: ipv{self.v} address exhausted")
    def __exit__(self, *_):
        if self.addr:
            with self.lock[self.v]:
                self.used.remove(self.addr)
                logger.info(f"ipv{self.v} return: {str(self.addr)}")

class Modem:
    def __init__(self, no: int, config: Tuple[List[str], Callable]):
        self.no = no
        self.readline_quit = False
        self.readlinebuf = []
        self.modem_proc: Union[subprocess.Popen, None] = None
        self.ppp_proc: Union[subprocess.Popen, None] = None
        self.modem_cmd, get_ppp_func = config
        self.ppp_func = get_ppp_func()
        self.modem_cmd = [*self.modem_cmd, "ATA"]
        self.pty_path = get_pty(self.no)
        if os.path.exists(self.pty_path):
            logger.warning(f"pty link exists {str(self.pty_path)}")
            self.pty_path.unlink()
        self.modem_ctl: threading.Thread = threading.Thread(target=self._start_modem_ctl, name=f"md{no:03d}")
        self.ppp_ctl: threading.Thread = threading.Thread(target=self._start_ppp_ctl, name=f"pp{no:03d}")
        self.modem_ctl.start()
        self.ppp_ctl.start()

    def _util_readline(self, dev: io.BufferedReader, timeout: float) -> bytes:
        readlinebuf = self.readlinebuf
        readlinebuf.clear()
        time_remaining = timeout
        while True:
            read_ready, _, _ = select.select([dev], list(), list(), 0.1)
            if self.readline_quit:
                self.readline_quit = False
                raise ReadLineException("process exit")
            if not read_ready:
                time_remaining -= 0.1
                if time_remaining < 0:
                    break
                continue
            readlinebuf.append(dev.read(1))
            if readlinebuf[-1] == b'\n':
                return b''.join(readlinebuf)
        return b''.join(readlinebuf)

    def _start_modem_ctl(self) -> None:
        while not ModemManager.terminating:
            try:
                if os.geteuid() == 0:
                    pw_record = pwd.getpwnam(RUN_AS)
                    uid, gid = pw_record.pw_uid, pw_record.pw_gid
                    def demote():
                        PR_SET_NO_NEW_PRIVS = 38
                        PR_CAP_AMBIENT = 47
                        PR_CAP_AMBIENT_CLEAR_ALL = 4
                        PR_GET_SECUREBITS = 27
                        PR_SET_SECUREBITS = 28
                        libc = ctypes.CDLL('libc.so.6')
                        libc.prctl.restype = ctypes.c_int
                        assert libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0
                        assert libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) == 0
                        assert libc.prctl(PR_SET_SECUREBITS, 0x2f) == 0
                        # SECBIT_KEEP_CAPS_LOCKED | SECBIT_NO_SETUID_FIXUP | SECBIT_NO_SETUID_FIXUP_LOCKED | SECBIT_NOROOT | SECBIT_NOROOT_LOCKED
                        assert libc.prctl(PR_GET_SECUREBITS) == 0x2f
                        os.setgroups([])
                        os.setresgid(gid, gid, gid)
                        os.setresuid(uid, uid, uid)
                        os.chdir(os.getcwd())
                    preexec_func = demote
                    env = {
                        'USER': pw_record.pw_name,
                        'LOGNAME': pw_record.pw_name,
                        'HOME': '/',
                        'PATH': os.environ['PATH'],
                    }
                else:
                    preexec_func = lambda: None
                    env = dict(os.environ)
                    logger.warning("d-modem is running as current user")
                    assert not PRODUCTION
                if PJSIP_V6:
                    env['PJSIP_IPV6'] = '1'
                else:
                    if 'PJSIP_IPV6' in env:
                        env.pop('PJSIP_IPV6')
                self.modem_proc = PopenComm(
                    [MODEMD, '-e', D_MODEM, str(self.pty_path)],
                    preexec_fn=preexec_func,
                    env = env
                )
                while (lines := self.modem_proc.readline()) is not None:
                    for line in lines:
                        logger.info(f"slmodemd: {line}")
                self.modem_proc.wait()
            except Exception:
                logger.exception("unknown error")
            try:
                self.readline_quit = True
                if self.ppp_proc:
                    self.ppp_proc.terminate()
                    self.ppp_proc.wait()
                    self.ppp_proc = None
            except Exception as err:
                logger.exception("unknown error")
        self.ppp_ctl.join()

    def _start_ppp_ctl(self) -> None:
        time.sleep(1)
        while not ModemManager.terminating:
            commands = self.modem_cmd.copy()
            try:
                with open(self.pty_path.resolve(), "rb", buffering=0) as ptyr, open(self.pty_path.resolve(), "wb", buffering=0) as ptyw:
                    tty.setraw(ptyr)
                    tty.setraw(ptyw)
                    while True:
                        while (got := self._util_readline(ptyr, 0.3).decode('utf-8', errors='replace')):
                            logger.debug(f"from pty: {got=}")
                            if got:
                                if (match := re.match(r'CONNECT ([0-9]+)', got)) and not self.ppp_proc:
                                    speed = int(match.groups()[0])
                                    assert speed > 0
                                    logger.info(f"connection {speed=}, start subprocess")
                                    ptyw.write(f"Remote speed {speed}\r\n".encode('utf-8'))
                                    try:
                                        with IP_Request(4) as ip4, IP_Request(6) as ip6:
                                            self.ppp_proc = self.ppp_func(ptyr, ptyw, speed, self.no, self.pty_path, (ip4, ip6))
                                            self.ppp_proc.wait()
                                    except Exception:
                                        logger.exception("unknown subprocess error")
                                    self.modem_proc.send_signal(signal.SIGINT)
                                    self.modem_proc.wait()
                                    raise PPPDDead
                                elif got == 'NO CARRIER\r\n':
                                    self._util_readline(ptyr, 2)
                                    raise CarrierLost
                        if commands:
                            cmd = commands.pop(0)
                            ptyw.write(f"{cmd}\n\r".encode('ascii'))
                            logger.info(f"command: {cmd}")
                            ptyw.flush()
                            time.sleep(0.1)
            except ModemManagerError as err:
                logger.info(f"pty detach: {repr(err)}")
                time.sleep(0.1)
            except Exception:
                logger.exception("unknown error")
                time.sleep(2)

if __name__ == "__main__":
    abspath=os.path.abspath(__file__)
    abspath=os.path.dirname(abspath)
    os.chdir(abspath)

    parser = argparse.ArgumentParser(description='ModemManager.py')
    parser.add_argument('-4', '--ipv4', type=str, default=str(IP4RANGE), help='ipv4 subnet')
    parser.add_argument('-6', '--ipv6', type=str, default=str(IP6RANGE), help='ipv6 subnet')
    parser.add_argument('-m', '--modemd', type=str, default=str(MODEMD), help='modemd location')
    parser.add_argument('-d', '--dmodem', type=str, default=str(D_MODEM), help='d-modem location')
    parser.add_argument('-p', '--pty', type=str, default=str(PTY_LOC), help='pty link location')
    parser.add_argument('-u', '--user', type=str, default=str(RUN_AS), help='run as user')
    parser.add_argument('-s', '--production', action='store_true', help='enable strict checks')
    parser.add_argument('--pjsip6', action='store_true', help='pjsip force v6')
    args = parser.parse_args()

    def g(x: str, y: Any) -> None:
        globals()[x] = y
    g('IP4RANGE', ipaddress.ip_network(args.ipv4))
    g('IP6RANGE', ipaddress.ip_network(args.ipv6))
    g('MODEMD', args.modemd)
    g('D_MODEM', args.dmodem)
    g('PTY_LOC', pathlib.Path(args.pty))
    g('RUN_AS', args.user)
    g('PRODUCTION', args.production)
    g('PJSIP_V6', args.pjsip6)
    if args.production:
        logger.setLevel(logging.INFO)

    modem_manger = ModemManager()

    def sighandler(signum, _frame):
        if ModemManager.terminating:
            logger.error(f"received signal {signum} while terminating")
        else:
            ModemManager.terminating = True
            logger.warning(f"terminate on signal {signum}")
            for modem in modem_manger.modems.values():
                modem.modem_proc.terminate()
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGABRT):
        signal.signal(sig, sighandler)

    for modem in modem_manger.modems.values():
        modem.modem_ctl.join()
