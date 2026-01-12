# PlayCord

_a discord bot for simple games_

by [@quantumbagel](https://github.com/quantumbagel)

### Sections to add to README

* Bot usage (within Discord)
* API usage (within Python)
* Features
* Dependencies
* List of games (both planned and implemented)

### Project Aims

PlayCord aims to become a bot capable of playing any paper/pencil game on Discord.
We will accomplish this using the following:

* Easy-to-understand API for creating games
* Backend syntax/caching handled by PlayCord
* SVG rendering
* MySQL database for leaderboard
* TrueSkill rating system

### Current TODOs

- [x] Need to read up on TrueSkill and fix Player.get_formatted_elo ✅ *Implemented in Player class*
- [x] Player order is currently randomized, this should be changed for some games (API) ✅ *Added PlayerOrder enum*
- [x] Emojis
    - [x] API support for registering emojis ✅ *register_emoji() function*
    - [x] API support for getting emojis ✅ *get_emoji() and get_all_emojis() functions*
    - [ ] Buttons need emojis
    - [ ] Rip off Tyler
- [ ] Prevent certain thread members (that aren't in game) from sending messages
- [ ] Heck, prevent anyone from just "sending messages" in game threads?
    - [x] From what I've found, this is impossible?
- [ ] Better permission checking for commands
    - [ ] This includes
        - [ ] the ability to start games (or inability)
        - [ ] the ability to join games (or inability)
    - [ ] Also, prevent the wrong move command from even bothering to check in the wrong channel and just failing it
- [x] Leaderboards ✅
    - [x] /leaderboard \<game\> command ✅
        - [x] top x, top worldwide, server ✅
        - [x] pagination, etc ✅
    - [ ] Top X globally ranked message in the get_formatted_elo function, etc
- [x] /help command for bot ✅
- [x] Textify more text areas, including ✅ *Added constants for:*
    - [x] Game started text ✅
    - [x] Button text ✅
    - [x] Game over text ✅
- [x] /playcord catalog <PAGE> for list of games, which is paginated ✅
- [x] /playcord profile <USER> for data on user ✅
- [ ] Rework the MySQL database to something else, because it SUCKS
- [x] Add analytic event system ✅ *Implemented in utils/analytics.py*
- [x] Cross-server matchmaking ✅ *Added infrastructure (GLOBAL_MATCHMAKING_QUEUE)*
- [x] Ability to change game settings (/playcord settings), such as the type of game, whether rated, and private status
  ✅
- [x] Add variables for ALL string fields in constants.py ✅
- [ ] API Docs
- [ ] Other games:
    - [ ] Liar's Dice
    - [ ] Poker (Texas Holdem)
    - [ ] Chess

If you find this project cool, I would love it if you starred my repository 🤩