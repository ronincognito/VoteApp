# Running the vote app on a Synology NAS with Docker

For a Synology with DSM 7.2 and Container Manager (Kong, a DS925+). The app runs in a Docker container, built on the NAS from this repo. The older setup without Docker (DSM 6.2, Godzilla) is in [Running App.py on Synology](Running%20App.py%20on%20Synology.md); the HTTPS part works the same way and is explained there in more detail.

The repo is cloned into `/volume1/docker/VoteApp`. The container gets the code from the image and keeps its data in `/volume1/docker/VoteApp/data` (the same `data/` folder as before: `votes_state.json` and `admin_config.json`), so rebuilding or removing the container doesn't touch the results or the admin code.

## Names used in these instructions

| Example | What it is |
|---|---|
| `Ron` | DSM user that runs the app (an administrator, for `sudo`) |
| `Kong.local` | the NAS on the home network |
| `yourclub.synology.me` | the DDNS address members use |

## First-time setup

1. In DSM, install **Container Manager** and **Git Server** (Package Center; Git Server provides the `git` command). Container Manager creates the shared folder `docker`. Switch on SSH (*Control Panel > Terminal & SNMP*). Give the NAS a fixed IP address in the Fritzbox.

2. SSH into the NAS and clone the repo (for a private repo, see the token in step 3 of the [other guide](Running%20App.py%20on%20Synology.md#first-time-setup)):
   ```
   $ ssh Ron@Kong.local
   $ cd /volume1/docker
   $ git clone https://github.com/ronincognito/VoteApp.git
   $ cd VoteApp
   ```

3. Create the `data` folder yourself, before the first start. If Docker has to create it, it belongs to root and the app can't write to it.
   ```
   $ mkdir -p data
   ```

4. Check your user and group numbers:
   ```
   $ id
   uid=1026(Ron) gid=100(users) ...
   ```
   The container runs as 1026:100 (`docker-compose.yml`), so the files in `data/` stay yours. It also gets the group `administrators` (101): the shared folder `docker` only gives write access to that group (DSM's ACL, the `+` in `ls -l`), and without it the app can't create files or folders in `data/`. If `id` shows other numbers, put them in a file `.env` next to `docker-compose.yml` (not on GitHub):
   ```
   APP_UID=1027
   APP_GID=100
   APP_ADMIN_GID=101
   ```
   (`APP_ADMIN_GID` is the number shown for `administrators` in the `groups=` part.)

5. Optional, when moving from the old NAS: copy the results and the admin code. Stop the app on the old NAS first, so the file doesn't change while copying. From the Mac:
   ```
   $ scp -O Ron@Godzilla.local:VoteApp/data/votes_state.json Ron@Godzilla.local:VoteApp/data/admin_config.json .
   $ scp -O votes_state.json admin_config.json Ron@Kong.local:/volume1/docker/VoteApp/data/
   ```

6. Build and start:
   ```
   $ sudo docker compose up -d --build
   ```
   The first build takes a few minutes (it downloads Python and installs the packages). Without `admin_config.json` the app creates a new admin code; see [Admin code](#admin-code).

7. Test on the home network: http://Kong.local:8080 and http://Kong.local:8080/admin.

The container starts by itself after a reboot (`restart: unless-stopped`), so no Task Scheduler task is needed.

Instead of step 6 you can also use the DSM interface: *Container Manager > Project > Create*, path `/docker/VoteApp`; it finds the existing `docker-compose.yml`. Both ways give the same project.

## Updating with the latest version from GitHub

```
$ ssh Ron@Kong.local
$ cd /volume1/docker/VoteApp
$ git pull
$ sudo docker compose up -d --build
```

The last command rebuilds the image (also installing a changed `requirements.txt`), stops the running container and starts the new one. It takes a few seconds when only the code changed. Rounds that were closed are kept; a round that is open at that moment comes back open but empty, as with any restart.

Old images pile up after many updates; clean them up now and then with `sudo docker image prune -f`.

Don't edit files in the repo on the NAS (except `.env` and `data/`); make changes on the Mac, commit, push, then pull.

## Starting, stopping, logs

From `/volume1/docker/VoteApp`:

| What | Command |
|---|---|
| stop | `sudo docker compose stop` |
| start again | `sudo docker compose start` |
| restart | `sudo docker compose restart` |
| status | `sudo docker compose ps` |
| log (Ctrl+C to quit) | `sudo docker compose logs -f` |

Or in DSM: *Container Manager > Container > voteapp*, with buttons for start/stop and a *Log* tab.

## Admin code

As in the [other guide](Running%20App.py%20on%20Synology.md#admin-code), the code is in `data/admin_config.json` (here `/volume1/docker/VoteApp/data/admin_config.json`) and can be changed there without a restart. A newly created code is printed once in the container log:
```
$ sudo docker compose logs | grep "admin code"
```

## HTTPS

Same as on the old NAS ([HTTPS setup](Running%20App.py%20on%20Synology.md#https-setup-how-it-works)), with the DSM 7 menu names:

- **DDNS:** *Control Panel > External Access > DDNS*.
- **Certificate:** *Control Panel > Security > Certificate*.
- **Reverse proxy:** *Control Panel > Login Portal > Advanced > Reverse Proxy*. Entry "VoteApp": source HTTPS `yourclub.synology.me` port 443, destination HTTP `localhost` port 8080.
- **Port forwarding:** 80 and 443 to this NAS. Don't forward 8080.

The container's port 8080 is published on the NAS itself, so the reverse proxy reaches it at `localhost:8080`, just as before.

### Testing HTTPS before the routers point to this NAS

While the old NAS still receives the internet traffic, the new one can be tested from the Mac:

1. Export the certificate on the old NAS (*Certificate*, select it, *Export*) and import it on the new one (*Add > Import*). Set the reverse proxy entry to it under *Settings*.
2. On the Mac, point the name to the new NAS for a moment: `sudo nano /etc/hosts`, add the line `192.168.178.xxx yourclub.synology.me` (the new NAS's IP).
3. Open https://yourclub.synology.me on the Mac: vote page, admin page, open a round and check that the vote page switches to the keypad.
4. Remove the line from `/etc/hosts` again.

Once the routers point to the new NAS, request a fresh Let's Encrypt certificate there (the imported one isn't renewed automatically) and assign it to the reverse proxy.
