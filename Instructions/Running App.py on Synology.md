# Running the vote app on a Synology NAS

These instructions are written for a Synology NAS with DSM. Much of it (git, Python, starting, stopping, updating, the admin code) also applies to other NAS brands and Linux systems; see [Other NAS or Linux systems](#other-nas-or-linux-systems) at the end for what to use instead of the Synology-specific parts.

The app lives in `~/VoteApp`, in the home folder of a DSM user on the NAS. That folder is a clone of the GitHub repo https://github.com/ronincognito/VoteApp. Make changes on the Mac, push them to GitHub, then pull them onto the NAS.

## Names used in these instructions

These are examples; replace them with your own.

| Example | What it is | Where to find yours |
|---|---|---|
| `ron` | DSM user that runs the app | the user you log in to DSM with |
| `SynologyNAS` | the NAS's server name | DSM: *Control Panel > Network > General*, "Server name" |
| `192.168.178.111` | the NAS's IP address on the home network | DSM: *Control Panel > Network > Network Interface* |
| `yourclub.synology.me` | the address members use, from anywhere | chosen when setting up DDNS, see [HTTPS setup](#https-setup-how-it-works) |

On the home network, the NAS can be reached by name as `SynologyNAS.local`. If a device can't find that name (some Android phones and Windows PCs), use the IP address instead. The name works through Bonjour, which DSM normally has on (*Control Panel > File Services*, *Advanced* tab).

Give the NAS a fixed IP address (in the Fritzbox: *Home Network > Network*, edit the NAS and tick "Always assign this network device the same IPv4 address"). The port forwarding in [HTTPS setup](#https-setup-how-it-works) points at this address, so it must not change.

## First-time setup

Only needed once, or when setting up the NAS from scratch.

1. SSH into the NAS (switch SSH on first in DSM: *Control Panel > Terminal & SNMP*, "Enable SSH service").
   ```
   $ ssh ron@SynologyNAS.local
   ```

2. Check that Python 3 and git are installed. If git is missing, install it via Package Center (search for "Git Server", or "Git" from the SynoCommunity package source).
   ```
   $ python3 --version
   $ git --version
   ```

3. Clone the repo into your home folder.
   ```
   $ cd ~
   $ git clone https://github.com/ronincognito/VoteApp.git
   $ cd VoteApp
   ```
   For a private copy of the repo, git asks for a username and password. Use your GitHub username, and as password a token: on github.com, *Settings > Developer settings > Personal access tokens > Fine-grained tokens*, limited to that repository with "Contents: Read-only". Run `git config --global credential.helper store` first, so git remembers the token (stored unencrypted in `~/.git-credentials`, acceptable for a read-only token). Tokens expire; renew it if git suddenly asks again.

4. Create the virtual environment and install the packages listed in `requirements.txt`. The venv is not stored on GitHub; it has to be created on each machine.
   ```
   $ python3 -m venv venv
   $ source venv/bin/activate
   $ pip install -r requirements.txt
   ```

## Starting the app

Normally the app is started by the Task Scheduler (see [Automatic start after a reboot](#automatic-start-after-a-reboot)). To start it by hand, for example to see its messages while testing:
```
$ ssh ron@SynologyNAS.local
$ cd ~/VoteApp
$ source venv/bin/activate
$ python app.py
```

Or to run it in the background (keeps running after you close the SSH session):
```
$ nohup python app.py &
```

Addresses (work from the club and from home):

| Page | Address |
|---|---|
| Vote page | https://yourclub.synology.me |
| Admin page | https://yourclub.synology.me/admin |

For testing on the home network only, the app can also be reached directly at http://SynologyNAS.local:8080 or http://192.168.178.111:8080 (and `/admin`). The app itself runs on port 8080 (see `app.py`); the HTTPS address goes through the Synology reverse proxy, see [HTTPS setup](#https-setup-how-it-works) below.

## Admin code

The admin page (`/admin`) asks for a code; the vote page stays open for everyone. After a correct code the device stays logged in for 30 days (**UITLOGGEN** at the bottom of the admin page logs out).

The code is in `~/VoteApp/data/admin_config.json` (not on GitHub). The file is created on the first start with a random 6-digit code, which the app prints once in its log (`~/VoteApp/nohup.out`, or the terminal when started by hand):
```
New admin code: 123456 (change it in /var/services/homes/ron/VoteApp/data/admin_config.json)
```
The file then looks like this:
```
{
  "admin_code": "123456",
  "secret_key": "..."
}
```

- **Change the code:** edit `admin_code` (e.g. `vi ~/VoteApp/data/admin_config.json` over SSH, or with a text editor via File Station). Takes effect immediately, no restart needed.
- **Log out all devices:** delete the `secret_key` line (or the whole file) and restart the app; a new key is generated. (Deleting the whole file also gives a new random code, see the log.)
- **Too many wrong codes:** after 5 wrong attempts the login is blocked for 15 minutes, for all devices together (behind the reverse proxy the app can't tell devices apart). A restart of the app lifts the block.

## Automatic start after a reboot

DSM: *Control Panel > Task Scheduler*. Triggered Task, user `ron` (not root, so the files in `data/` stay writable when the app is started by hand over SSH), event Boot-up, with this script:
```
cd /var/services/homes/ron/VoteApp
source venv/bin/activate
nohup python app.py &
```

The first line needs the full path of the app folder. Check it over SSH with `cd ~/VoteApp && pwd`; the home folder name may start with a capital (e.g. `/var/services/homes/Ron/VoteApp`), and the path must match exactly.

To start the app the same way without rebooting: select the task and click **Run**. (Stop a running copy first, otherwise the new one can't use port 8080.)

## Stopping the app

> **Note:** the results are saved in `~/VoteApp/data/votes_state.json` when a round is opened or closed (and on clearing or the vote-limit toggle), and read back when the app starts. Stopping, restarting or a reboot doesn't lose the closed rounds. Individual votes are not saved, so votes don't have to wait for the disk: if the app stops *during* an open round, that round comes back open but empty, and members just vote again. This file is not on GitHub (it's in `.gitignore`), so `git pull` leaves it alone. To start completely fresh, use *Wis alle data* on the admin page (or stop the app and delete the file).

If it runs in the foreground, press Ctrl+C. If it runs in the background (also when started by the Task Scheduler):
```
$ pkill -f "python app.py"
```

Or find the process and stop it yourself:
```
$ ps aux | grep "app.py" | grep -v grep
$ kill <PID>
```

There should be one python process. (If there are two, debug mode is on in `app.py`; killing the first one stops both.) Use `kill -9 <PID>` only if the process doesn't stop. Run the `ps` command again to check: no output means the app is stopped.

## Updating the app with the latest version from GitHub

1. Stop the app:
   ```
   $ pkill -f "python app.py"
   ```
2. Pull the latest code:
   ```
   $ cd ~/VoteApp
   $ git pull
   ```
3. Only if `requirements.txt` was changed, update the packages:
   ```
   $ source venv/bin/activate
   $ pip install -r requirements.txt
   ```
4. Start the app again: *Task Scheduler > select the task > Run*.

Don't edit files directly on the NAS. Make changes on the Mac, commit and push them, then `git pull` on the NAS. Otherwise `git pull` may refuse to run because of local changes.

## Backup of the results

The CSV download on the admin page is the easiest backup. The complete state (all rounds, voter numbers, settings) is in `~/VoteApp/data/votes_state.json`; copy that file to keep it. If the app ever finds the file unreadable, it renames it to `votes_state.json.broken-<date-time>` (so nothing is lost) and starts empty; see `nohup.out` for the message.

## HTTPS setup (how it works)

Phones connect securely to https://yourclub.synology.me. The connection goes through two routers to the Synology, where a reverse proxy decrypts it and passes it on to the app on port 8080:
```
phone > internet > KPN Experia box > Fritzbox > Synology reverse proxy (443) > app (8080)
```

1. **Address:** *Control Panel > External Access > DDNS*. Provider Synology, hostname `yourclub.synology.me`. The Synology keeps this name pointed at the home WAN address, also when KPN changes it.

2. **Certificate:** *Control Panel > Security > Certificate*. Let's Encrypt certificate for `yourclub.synology.me` (created when the DDNS entry was saved). DSM renews it automatically every few months; this needs port 80 to stay forwarded. Under *Configure*, the `yourclub.synology.me` service (the reverse proxy) uses this certificate.

3. **Reverse proxy:** *Control Panel > Application Portal > Reverse Proxy* tab. Entry "VoteApp":
   ```
   Source:       HTTPS  yourclub.synology.me  port 443
   Destination:  HTTP   localhost        port 8080
   ```
   (Type the values in; the grey text in empty fields is only an example.)

4. **Port forwarding,** on both routers (TCP, IPv4):
   ```
   KPN Experia box:  443 and 80 > Fritzbox (its address on the KPN network)
   Fritzbox:         443 and 80 > the NAS (its fixed IP, e.g. 192.168.178.111)
                     (Internet > Permit Access > Port Sharing)
   ```
   Port 8080 should **not** be forwarded on either router, so the app can only be reached from the internet via HTTPS.

The live updates on the vote page (keypad appearing when a round opens) use `/events`. `app.py` sends extra headers and a keepalive every 15 seconds so these updates get through the reverse proxy.

Safari on iPhone doesn't show a lock icon for secure sites; it only warns ("Niet veilig") when a site is not secure.

## Other NAS or Linux systems

The steps for git, the venv, starting, stopping, updating, the admin code and the port forwarding work on any Linux system with SSH, Python 3.8 or newer and git. Replace the Synology-specific parts:

| Synology | Elsewhere |
|---|---|
| Package Center (Python, git) | the system's package manager, e.g. `sudo apt install python3 python3-venv git` |
| Task Scheduler (start at boot) | a systemd service (Raspberry Pi, Linux server), or the NAS's own startup scripts (QNAP: `autorun.sh`) |
| DDNS `synology.me` | the brand's own service (QNAP: myQNAPcloud), or a free one such as DuckDNS |
| Certificate and reverse proxy in DSM | the NAS's own reverse proxy (QNAP has one), or Caddy or nginx, which can also get the Let's Encrypt certificate |

On TrueNAS and Unraid, apps normally run in Docker rather than directly on the system; the repo's `Dockerfile` and `docker-compose.yml` can be used there, see [Running on Synology with Docker](Running%20on%20Synology%20with%20Docker.md).

## Notes

The setup on macOS is the same as on the Synology, except for the SSH step and the IP address. See [Running App.py on macOS](Running%20App.py%20on%20macOS.md).

If the folder is ever renamed or moved, the venv stops working (it contains the full folder path). Fix it by deleting and recreating it:
```
$ rm -rf venv
$ python3 -m venv venv
$ source venv/bin/activate
$ pip install -r requirements.txt
```

Suppressing terminal log messages for the `/vote_count` API calls is done in the `app.py` code (look for `logging.Filter` or `werkzeug`).
