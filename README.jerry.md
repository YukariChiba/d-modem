# D-Modem
Software modem with **sound**  
For burble's retro voip modem https://burble.dn42/retro/modem/  
Now also for JerryXiao's own software voip modems!

Fortunately this old proprietary software modem still works :)

## Prerequisite
- A desktop GNU/Linux distribution, livecd is also OK
- See the original README
- Modem without any sound isn't a real modem? No problem, pulseaudio is used to output modem sound in realtime. You must have pulseaudio dev package installed on your system to compile this software.

## Step-by-step instruction

Execute these commands as a normal user:  

```
make
./slmodemd/slmodemd -e ./d-modem
screen /tmp/ttySL0 115200
```

Inside the serial console, use the following at commands:  

```
ATX3
> OK
AT+MS=22,1
> OK
ATD424026019@voip.burble.dn42
> CONNECT 1200
```

You can also connect to:
| number | AT+MS | ATD | Modulation |
| ------------ | ------------ | ------------ | ------------ |
| (42403618)361800 | AT+MS=90,1 | ATD361800@pbx.jerry.dn42 | V.90 |
| (42403618)361801 | AT+MS=34,1 | ATD361801@pbx.jerry.dn42 | V.34 |
| (42403618)361802 | AT+MS=32,1 | ATD361802@pbx.jerry.dn42 | V.32 |
| (42403618)361803 | AT+MS=22,1 | ATD361803@pbx.jerry.dn42 | V.22 |
| (42403618)361804 | AT+MS=22,1 | ATD361804@pbx.jerry.dn42 | V.22 |
| (42403618)361805 | AT+MS=23,1 | ATD361805@pbx.jerry.dn42 | V.23 |
| (42403618)361806 | AT+MS=23,1 | ATD361806@pbx.jerry.dn42 | V.23 |

Now you should be connected. If you see a login prompt, go to https://burble.dn42/retro/modem/ for login username and password. Once logged in, close out of GUN Screen and execute pppd like this:  

```
sudo pppd $(readlink /tmp/ttySL0) 1200 user dn42 noauth nodefaultroute nodetach
```

nodefaultroute is used here to keep your voip alive :)  

## netns
If you would like a full dialup dn42 experience, try using netns:  

```
sudo ip netns add modem
sudo ip netns exec modem pppd $(readlink /tmp/ttySL0) 1200 noauth nodefaultroute nodetach
```

Start a new terminal and  
```
sudo ip netns exec modem $SHELL
unshare -m
echo 172.23.0.53 > /tmp/resolv.conf
mount --bind /tmp/resolv.conf /etc/resolv.conf
ping burble.dn42
```

Have fun!


## Server
This code can also work as a server, no docs available though ;)  
Just type ATA in the modem console and try it out first.  
