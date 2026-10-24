# Running the vote app on a Mac

Works on any Mac. Useful for testing changes before pushing them to GitHub and pulling them onto the NAS. The Mac version has its own `data/` folder, so test votes and test rounds never end up on the NAS.

## Addresses

On the Mac itself: http://localhost:8080 (vote page) and http://localhost:8080/admin (admin page).

From phones and other computers, use the Mac's local name, `ComputerMacOS.local` in this example:

| Page | Address |
|---|---|
| Vote page | http://ComputerMacOS.local:8080 |
| Admin page | http://ComputerMacOS.local:8080/admin |

Find the Mac's local name in *System Settings > General > Sharing*, under *Local hostname* at the bottom (or run `scutil --get LocalHostName` and add `.local`). The name keeps working when the router gives the Mac a different IP address.

iPhones and iPads find `.local` names; some Android phones don't. Use the Mac's IP address instead, for example http://192.168.178.102:8080. Find it with:
```
$ ipconfig getifaddr en0
```
(No output: try `en1`. Or look in *System Settings > Wi-Fi* or *Network > Details*.)

These addresses only work on the home network. The admin code is printed in the terminal on the first start, and stored in `data/admin_config.json` (see the Synology instructions, section "Admin code").

## First-time setup

Clone the repo (or use an existing clone) and create the virtual environment with the packages from `requirements.txt`:
```
$ git clone https://github.com/ronincognito/VoteApp.git
$ cd VoteApp
$ python3 -m venv venv
$ source venv/bin/activate
$ pip install -r requirements.txt
```

The first time the app runs, macOS may ask whether Python may accept incoming network connections. Click *Allow*, otherwise phones can't reach the app.

## Starting

```
$ cd VoteApp
$ source venv/bin/activate
$ python app.py
```

Stop it with Ctrl+C. `deactivate` leaves the virtual environment.

To keep it running after closing the terminal window:
```
$ nohup python app.py &
```

## Checking and stopping

Check that the app answers (shows `{"voting_open": false}` or `true`):
```
$ curl http://localhost:8080/voting_status
```

Stop an app running in the background:
```
$ pkill -f "python app.py"
```
Or find the process with `ps aux | grep "app.py" | grep -v grep` and stop it with `kill <PID>` (`kill -9 <PID>` only if it doesn't stop).

## Background info

The app listens on all network addresses (`0.0.0.0`) on port 8080, set in the `app.run(...)` line at the bottom of `app.py`. Flask normally only listens on `localhost`, which only the computer itself can reach.

Debug mode (detailed error pages, automatic reload when the code changes) is switched with `debug=` in that same line. It is `False` in the repo; set it to `True` only temporarily for testing on the Mac, and don't commit that.

If the Mac says `Address already in use`, another copy of the app is still running; stop it first (see above).
