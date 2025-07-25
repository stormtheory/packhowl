<div align="center">
  <img width="280" height="280" alt="Image" src="https://github.com/user-attachments/assets/9b9ceaa1-c694-4b55-869f-b0ff54954fe9" />
</div>
<h1 align="center">Pack Howl</h1>
<h3 align="center">Secure voice and chat, only your Pack can hear.</h3>

Encrypted, self-hosted, private voice + chat client and server written by StormTheory using Python3.7, PySide6, sounddevice, and opuslib encoder/decoder. 
Focused on trusted group communications with mutual identity/auth TLS layers, so you know it is your Pack.

Please submit all problems/issues/sugeestions to https://github.com/stormtheory/packhowl/issues


<img width="960" height="528" alt="Screenshot" src="https://github.com/user-attachments/assets/2e5233bf-a033-417d-9f5d-61870d3652b5" />


- ✅ Private, secure, self-hosted [✔] no ads [✔] no spy-ware [✔] opensource [✔] no cost [✔] no third parties requiring login.
- ✅ Low Server CPU and memory use. [✔] Fast and powerful.
- ✅ Linux based server and client. [✔] Will listen to requests for creating a windows client app.
- ✅ Secure, TLS-only, within your trusted enclave with shared Auth certs.
- ✅ Clients send encoded PCM with Opus to the server via TLS encrypted tunnel and server only forwards to other clients.
- ✅ Binary-safe Opus forwarding supported using opuslib encoder/decoder.

# In the Works:
Guiding Principle(s):
- As always: Fighting to keep this project: secure, free, private, opensource, fast, and easy (in that order).

Short-term:
- ✅ Getting a stable and tested product.
- ✅ Adding a text sizing dropdown for chatbot text size.
- ✅ Settings button, to re-run firsttime startup.
- ✅ Debian installer.
- [ ] Audio Tones for join, message alert, disconnect, and alerts

Long-term:
- Supporting project and fan-base.

Upon Request:
- Add YUM support.
- Add AMD support.

# System Requirements:
- Python3.7 or better.
- TLS 1.3 support.
- Ubuntu/Debian/Mint are only tested to be supported.

App could work on RHEL/Rocky/CentOS, no Yum/DNF package support yet. 
Please feedback if you want a YUM/DNF .rpm package. 
If there is interest in other Linux flavors/families please let me know or it's just a project for me and my family :P as our daily drivers. 

# INSTALL:
 The client run script will create(if not present) or open the virtual Enviorment needed for the client to run.
 Note you will need at least 500M of /tmp space available for the first time startup install.
 Virtual environment may take up 800M of space for all needed packages for client. 
 The server is 68K becuase it doesn't require any pip packages just one simple and small python server.py script.

1) Download the latest released .deb package files off of github at https://github.com/stormtheory/packhowl/releases and install on your system. There is both a pack-howl.deb and pack-howl-server.deb and use as needed.

        # Create your SSL Certs for Encryption and Auth
            # On the server:
                 cd /opt/packhowl
                 ./generate_keys.sh -n <client hostname(s)>
            
    - SSL Certs will automatically install on the server. 
    - For the clients you will have to manually move the <hostname>.pem and ca.pem to the clients.
    - The SSL certs can be found at /etc/ssl/packhowl/ or /var/lib/packhowl/.packhowl/certs/
    - Move the 2 certs, <hostname>.pem and ca.pem to each of the clients.
    - Only 2 is needed per client and each has their own .pem and a ca.pem.

            # On Client as user:
                /opt/packhowl/desktop_icon_installer.sh
                /opt/packhowl/run_client.sh
            # Script will create data directory. Place SSL certs in the directory and named the way in the output message.


2) Manual Install without Package Manager, run commands:

	Download the zip file of the code, off of Github. This is found under the [<> Code] button on https://github.com/stormtheory/packhowl.

	Extract directory from the zip file. Run the following commands within the directory.

        # Install script for server:
            chmod 700 setup-server.sh
            ./setup-server.sh
               # run through setup script and install system user and generate keys and start server.
               # Edit config.py as needed for many different options.

        # Client install:
            mkdir -p ~/.packhowl/certs
               # Install ca.pem and {hostname}.pem that was generated from the server in your ~/.packhowl/certs
            ./desktop_icon_installer.sh
            ./run_client.sh

# RUN:
### run the local Windowed App

        /opt/packhowl/run_client.sh

### run the server
    
    When offically installed with the DEB/YUM/DNF package manager installer:
    This is executed by the service packhowl.service who using the system user packhowl runs command /opt/packhowl/run_server.sh

# For added security:
    - run server within AppArmor, SELinux, or firejail sandbox. Use a chroot jail or container like Docker.
    - run server as a seprate user not root or a regular user. (Default)

# User Agreement:
This project is not a company or business. By using this project’s works, scripts, or code know that you, out of respect are entitled to privacy to highest grade. This product will not try to steal, share, collect, or sell your information. However 3rd parties such at Github may try to use your data without your consent. Users or admins should make reports of issue(s) related to the project’s product to the project to better equip or fix issues for others who may run into the same issue(s). By using this project’s works, scripts, code, or ideas you as the end user or admin agree to the GPL-2.0 License statements and acknowledge the lack of Warranty. As always, give us a Star on Github if you find this useful, and come help us make it better.

As stated in the GPL-2.0 License:
    "This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details." Also "ABSOLUTELY NO WARRANTY".
