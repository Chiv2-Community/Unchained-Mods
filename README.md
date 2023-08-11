# ArgonSDK-Mods
> TODO: this repo will be included as a submodule to ArgonSDK at some point

# Overview

## Launching Mods
Use following console command to launch a map with specified mods:

`open agmods?map<map_name>?mods=<comma_separated_mods>`

e.g. `open agmods?map=ffa_wardenglade?mods=ExampleMod,GiantSlayers,Peasants`

# Installation
## [TEMPORARY] Adding this repo to UE Project Content directory
1. Create `Mods` folder inside the Content directory of ArgonSDK and open it
2. Clone this repository using following command:
  `git clone git@github.com:Chiv2-Community/ArgonSDK-Mods.git ArgonSDK`
> You should now have a folder named ArgonSDK (`<Main Repo dir>/Content/ArgonSDK`)

# Creating a mod
## Creating a mod Actor
> An example mod can be found [here](https://github.com/Chiv2-Community/ArgonSDK-ExampleMod).

1. Navigate to `Content/Mods/AgMods` inside UE4 Editor

![image](https://github.com/Chiv2-Community/ArgonSDK-Mods/assets/134062045/bd0ecf7e-95e5-4937-900b-8d0f66197487)

2. Create a new folder. This name will be used to load our mod
3. Inside your folder, right click and create a new _Blueprint Class_ of type `ArgonSDKModBase`

> Names of the folder and your newly created asset must be equal!

![image](https://github.com/Chiv2-Community/ArgonSDK-Mods/assets/134062045/6677265f-b955-4643-a6aa-4a590cac5e3d)

## Implementing Mod Logic
1. Open your Blueprint
2. Update Mod info variables. These will be displayed when a map loads

![image](https://github.com/Chiv2-Community/ArgonSDK-Mods/assets/134062045/20eb9666-beb3-4010-9426-50ab8579d963)

> Setting SilentLoad to true will hide the loading notification ingame

3. Switch to the Event Graph, you should now have access to following events

![image](https://github.com/Chiv2-Community/ArgonSDK-Mods/assets/134062045/98945913-d280-4756-b865-113c4bd19cd5)

> Check the example mod for more info
