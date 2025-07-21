# Silent Link

Encrypted, private voice + chat client and server written in Python 3.12 using PySide6, sounddevice, and TLS 1.3 mutual auth.

# In the Works:
Guiding Principle(s):
- As always: Fighting to keep this AI free, private, opensource, fast, and easy (in that order).

Short-term:
- 

Long-term:
- 

Upon Request:
- Add YUM support.
- Add AMD support.

# Ultimate Goals
- A free, private, fast, and easy.

# System Requirements:
- TLS 1.3 support.
- Ubuntu/Mint is only tested to be supported.

App could work on RHEL/Rocky/CentOS, no Yum/DNF package support yet. 
Please feedback if you want a YUM/DNF .rpm package. 
If there is interest in other Linux flavors/families please let me know or it's just a project for me and my family :P as our daily drivers. 

# INSTALL:
 Run scripts will create(if not present) or open the virtual Enviorment needed for AI tools to run.
 Note you will need at least 4G of /tmp space available for the first time startup install.
 Virtual environment may take up 7Gbs of space for all needed packages.

1) Download the latest released .deb package file off of github and install on your system.
2) Build DEB Install file:
	
	Download the zip file of the code, off of Github. This is found under the [<> Code] button on https://github.com/stormtheory/silent-link.
	
	Extract directory from the zip file. Run the build script in the directory. 

        ./build

   	Install the outputted .deb file.

3) Install without Package Manager, run commands:

	Download the zip file of the code, off of Github. This is found under the [<> Code] button on https://github.com/stormtheory/silent-link.

	Extract directory from the zip file. Run the following commands within the directory.

        # Install script for llama3 LLM:
        friday/install_ollama.sh

        # If you want to try the French Fully-Opensource LLM Mistral then:
        friday/install_mistral_from_TheBloke.sh

# RUN:
### run the local Windowed App


### run the server



# User Agreement:
This project is not a company or business. By using this project’s works, scripts, or code know that you, out of respect are entitled to privacy to highest grade. This product will not try to steal, share, collect, or sell your information. However 3rd parties such at Github may try to use your data without your consent. Users or admins should make reports of issue(s) related to the project’s product to the project to better equip or fix issues for others who may run into the same issue(s). By using this project’s works, scripts, code, or ideas you as the end user or admin agree to the GPL-2.0 License statements and acknowledge the lack of Warranty. As always, give us a Star on Github if you find this useful, and come help us make it better.

As stated in the GPL-2.0 License:
    "This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details." Also "ABSOLUTELY NO WARRANTY".
