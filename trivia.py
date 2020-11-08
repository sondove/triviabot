#!/usr/bin/python2
# Copyright (C) 2013 Joe Rawson
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# The bot should respond to /msgs, so that users can check their scores,
# and admins can give admin commands, like die, show all scores, edit
# player scores, etc. Commands should be easy to implement.
#
# Every so often between questions the bot should list the top ranked
# players, wait some, then continue.
#

import json
import string
import os
import sys
import datetime
from os import execl, listdir, path, makedirs
from random import choice
from twisted.words.protocols import irc
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from twisted.internet.task import LoopingCall

from lib.answer import Answer

import config

if not os.path.exists(config.SAVE_DIR):
    os.makedirs(config.SAVE_DIR)

if config.USE_SSL.lower() == "yes":
    from twisted.internet import ssl
elif config.USE_SSL.lower() != 'no':
    # USE_SSL wasn't yes and it's not no, so raise an error.
    raise ValueError("USE_SSL must either be 'yes' or 'no'.")

# Determine text color
try:
    config.COLOR_CODE
except:
    config.COLOR_CODE = ''


class triviabot(irc.IRCClient):
    '''
    This is the irc bot portion of the trivia bot.

    It implements the whole program so is kinda big. The algorithm is
    implemented by a series of callbacks, initiated by an admin on the
    server.
    '''

    def __init__(self):
        self._answer = Answer()
        self._question = ''
        self._scores = {}
        self._teams = {}

        self._round_questions = config.ROUND_QUESTIONS
        self._round_scores = {}
        self._round_started = False
        self._round_question_num = 0

        self._clue_number = 0
        self._admins = list(config.ADMINS)
        self._admins.append(config.OWNER)
        self._game_channel = config.GAME_CHANNEL
        self._team_limit = config.TEAM_LIMIT
        self._current_points = 5
        self._questions_dir = config.Q_DIR
        self._lc = LoopingCall(self._play_game)
        self._quit = False
        self._restarting = False

        self._load_game()
        self._votes = 0
        self._voters = []

    def _get_nickname(self):
        return self.factory.nickname

    nickname = property(_get_nickname)

    def _get_lineRate(self):
        return self.factory.lineRate

    lineRate = property(_get_lineRate)

    def _cmsg(self, dest, msg):
        """
        Write a colorized message.
        """

        self.msg(dest, "{}{}".format(config.COLOR_CODE, msg))

    def _gmsg(self, msg):
        """
        Write a message to the channel playing the trivia game.
        """

        self._cmsg(self._game_channel, msg)

    def _play_game(self):
        '''
        Implements the main loop of the game.
        '''
        qn = self._round_question_num
        if qn > config.ROUND_QUESTIONS:
          self._gmsg("Round complete!")
          self._stop()

        points = {0: 5,
                  1: 3,
                  2: 2,
                  3: 1
                  }
        if self._clue_number == 0:
            #self._lc.stop()
            #self._lc.start(config.WAIT_INTERVAL)

            self._round_question_num += 1
            qn = self._round_question_num
            self._votes = 0
            self._voters = []
            self._get_new_question()
            self._current_points = points[self._clue_number]
            # Blank line.
            self._gmsg("")
            #self._gmsg("Next question:")
            self._gmsg("Next Question [{}/{}]:".format(qn, self._round_questions))
            self._gmsg(self._question)
            self._gmsg("Clue: {}".format(self._answer.current_clue()))
            self._clue_number += 1
        # we must be somewhere in between
        elif self._clue_number < 4:
            self._current_points = points[self._clue_number]
            self._gmsg("Question [{}/{}]:".format(qn, self._round_questions))
            self._gmsg(self._question)
            self._gmsg("Clue: {}".format(self._answer.give_clue()))
            self._clue_number += 1
        # no one must have gotten it.
        else:
            self._gmsg("No one got it. The answer was: {}"
                       .format(self._answer.answer))
            self._clue_number = 0

            qn = self._round_question_num
            if qn >= config.ROUND_QUESTIONS:
              self._gmsg("Round complete!")
              self._stop()
            else:
              self._gmsg("Next question in {} seconds.".format(config.WAIT_QUESTION))
              self._get_new_question()

            #self._lc.reset()
            #self._lc.stop()
            #self._lc.start(config.WAIT_QUESTION, False)

    def signedOn(self):
        '''
        Actions to perform on signon to the server.
        '''
        self.join(self._game_channel)
        self.msg("NickServ", "identify {}".format(config.IDENT_STRING))
        print("Signed on as {}.".format(self.nickname))
        if self.factory.running:
            self._start(None, None, None)
        else:
            self._gmsg("Welcome to {}!".format(self._game_channel))
            self._gmsg("Have an admin start the game when you are ready.")
            self._gmsg("For how to use this bot, just say !help or")
            self._gmsg("{} help.".format(self.nickname))

    def joined(self, channel):
        '''
        Callback runs when the bot joins a channel
        '''
        print("Joined {}.".format((channel)))

    def privmsg(self, user, channel, msg):
        '''
        Parses out each message and initiates doing the right thing
        with it.
        '''
        user, temp = user.split('!')
        print(user + " : " + channel + " : " + msg)
        # need to strip out non-printable characters if present.
        printable = string.printable
        msg = ''.join(filter(lambda x: x in printable, msg))

        # parses each incoming line, and sees if it's a command for the bot.
        try:
            if (msg[0] == "!"):
                command = msg.replace('!', '').split()[0]
                args = msg.replace('!', '').split()[1:]
                self.select_command(command, args, user, channel)
                return
            elif (msg.split()[0].find(self.nickname) == 0):
                command = msg.split()[1]
                args = msg.replace(self.nickname, '').split()[2:]
                self.select_command(command, args, user, channel)
                return
            # if not, try to match the message to the answer.
            else:
                if msg.lower().strip() == self._answer.answer.lower():
                #if msg.lower().strip() == self._answer.answer.lower() or \
                #  msg.lower().strip() == 'wtf':
                    self._winner(user, channel)
                    self._save_game()
        except Exception as e:
            print(e)
            return

    def _winner(self, user, channel):
        '''
        Congratulates the winner for guessing correctly and assigns
        points appropriately, then signals that it was guessed.
        '''
        if channel != self._game_channel:
            self.msg(channel,
                     "I'm sorry, answers must be given in the game channel.")
            return

        # get team
        in_team = True
        tn = 'noteam'
        try:
          tn = self._teams['users'][user]
        except:
          in_team = False

        if in_team:
          self._gmsg("{} ({}) GOT IT!".format(tn.upper(), user.upper()))
        else:
          self._gmsg("{} GOT IT!".format(user.upper()))

        self._gmsg("""If there was any doubt, the correct answer was: {}""".format(self._answer.answer))


        try:
            self._scores['user'][user] += self._current_points
            if in_team:
              self._scores['team'][tn] += self._current_points
        except:
            self._scores['user'][user] = self._current_points
            if in_team:
              self._scores['team'][tn] = self._current_points
        if self._current_points == 1:
            self._gmsg("{} point has been added to your score!"
                       .format(str(self._current_points)))
        else:
            self._gmsg("{} points have been added to your score!"
                       .format(str(self._current_points)))

        self._lc.stop()
        #self._lc.start(config.WAIT_QUESTION, False)
        self._lc.start(config.WAIT_INTERVAL, False)

        self._clue_number = 0

        qn = self._round_question_num
        if qn >= config.ROUND_QUESTIONS:
          self._gmsg("Round complete!")
          self._stop()
        else:
          self._gmsg("Next question in {} seconds.".format(config.WAIT_QUESTION))

    def ctcpQuery(self, user, channel, msg):
        '''
        Responds to ctcp requests.
        Currently just reports them.
        '''
        print("CTCP recieved: " + user + ":" + channel +
              ": " + msg[0][0] + " " + msg[0][1])

    def _leave_util(self, channel, team, user):
      """
      """
      dst = user
      if not channel == self.nickname:
        dst = channel

      ot = team
      self._teams['teams'][ot]['members'] = [x for x in self._teams['teams'][ot]['members'] if x != user]
      if len(self._teams['teams'][ot]['members']) == 0:
        del self._teams['teams'][ot]
        self._cmsg(dst, '{} left "{}".'.format(user, ot))
      else:
        if self._teams['teams'][ot]['owner'] == user:
          no = self._teams['teams'][ot]['members'][0]
          self._teams['teams'][ot]['owner'] = no
          self._cmsg(dst, '{} left "{}", {} is the new owner.'.format(user, ot, no))
        else:
          self._cmsg(dst, '{} left "{}".'.format(user, ot))

      self._save_game()

    def _leave(self, args, user, channel):
      """
      leave your current team
      """
      self._leave_util(channel, self._teams['users'][user], user)

    def _kick(self, args, user, channel):
      """
      Kick's a user from your team if you are an admin
      """

      ku = args[0]

      dst = user
      if not channel == self.nickname:
        dst = channel

      t = self._teams['users'][user]
      if self._teams['teams'][t]['owner'] == user:
        if ku in self._teams['teams'][t]['members']:
          self._leave_util(channel, t, ku)
        else:
          self._cmsg(dst, '{}: {} is not in your team!'.format(user, ku))
      else:
        self._cmsg(dst, '{}: you are not the owner of "{}"!'.format(user, t))

    def _join(self, args, user, channel):
        '''
        User want's to join a team
        '''
        dst = user
        if not channel == self.nickname:
          dst = channel

        print('\n\n\n')
        print('---')
        print(self._teams)

        tn = ' '.join(args)
        tn = [ch for ch in tn if ch.isalnum() or ch == ' ']
        tn = ''.join(tn)
        tn = tn[:25]

        print(tn)


        if 'users' not in self._teams:
          self._teams['users'] = {}

        if 'teams' not in self._teams:
          self._teams['teams'] = {}

        if tn not in self._teams['teams'].keys():
          o = {}
          o['owner'] = user
          o['name'] = tn
          o['members'] = []

          self._teams['teams'][tn] = o

        else:
          if user in self._teams['teams'][tn]['members']:
            self._cmsg(dst, '{}: you are already in "{}".'.format(user, tn))
            return

          if len(self._teams['teams'][tn]['members']) >= self._team_limit:
            self._cmsg(dst, '{}: Team "{}" is full, someone needs to leave before you can join.'.format(user, tn))
            return

        # if user is in a team, leave it
        if user in self._teams['users'].keys():

          ot = self._teams['users'][user]
          if ot in self._teams['teams'].keys():
            self._leave_util(channel, ot, user)

        self._teams['teams'][tn]['members'].append(user)
        self._teams['users'][user] = tn

        self._cmsg(dst, '{} has joined "{}".'.format(user, tn))

        print(self._teams)

        self._save_game()

    def _list_teams(self, args, user, channel):
      """
      List all teams? Capped at 25?
      """

      dst = user
      if not channel == self.nickname:
        dst = channel

      for t in self._teams['teams'].keys():
        self._cmsg(dst, '{}: {}'.format(t, ', '.join(self._teams['teams'][t]['members'])))




    def _help(self, args, user, channel):
        '''
        Tells people how to use the bot.
        Replies differently if you are an admin or a regular user.
        Only responds to the user since there could be a game in
        progress.
        '''
        dst = user
        if not channel == self.nickname:
          dst = channel

        try:
            self._admins.index(user)
        except:
            self._cmsg(dst, "I'm {}'s trivia bot.".format(config.OWNER))
            self._cmsg(dst, "Commands: score, standings, help, join, leave")

            return
        self._cmsg(dst, "I'm {}'s trivia bot.".format(config.OWNER))
        self._cmsg(dst, "Commands: score, standings, giveclue, help, next, "
                   "skip ")
        self._cmsg(dst, "Admin commands: die, set <user> <score>, start, stop, "
                   "save")

    def _show_source(self, args, user, channel):
        '''
        Tells people how to use the bot.
        Only responds to the user since there could be a game in
        progress.
        '''
        self._cmsg(user, 'My source can be found at: '
                   'https://github.com/rawsonj/triviabot')

    def select_command(self, command, args, user, channel):
        '''
        Callback that responds to commands given to the bot.

        Need to differentiate between priviledged users and regular
        users.
        '''
        # set up command dicts.
        unpriviledged_commands = {'score': self._score,
                                  'help': self._help,
                                  #'source': self._show_source,
                                  'standings': self._standings,
                                  'join': self._join,
                                  'leave': self._leave,
                                  'kick': self._kick,
                                  'teams': self._list_teams,
                                  #'giveclue': self._give_clue,
                                  #'next': self._next_vote,
                                  #'skip': self._next_question
                                  }
        priviledged_commands = {'die': self._die,
                                'restart': self._restart,
                                'reset': self._reset,
                                'set': self._set_user_score,
                                'start': self._start,
                                'stop': self._stop,
                                'save': self._save_game,
                                }
        print(command, args, user, channel)
        try:
            self._admins.index(user)
            is_admin = True
        except:
            is_admin = False

        # the following takes care of sorting out functions and
        # priviledges.
        if not is_admin and command in priviledged_commands:
            self.msg(channel, "{}: You don't tell me what to do."
                     .format(user))
            return
        elif is_admin and command in priviledged_commands:
            priviledged_commands[command](args, user, channel)
        elif command in unpriviledged_commands:
            unpriviledged_commands[command](args, user, channel)
        else:
            self.describe(channel, "{}looks at {} oddly."
                          .format(config.COLOR_CODE, user))

    def _next_vote(self, args, user, channel):
        '''Implements user voting for the next question.

        Need to keep track of who voted, and how many votes.

        '''
        if not self._lc.running:
            self._gmsg("We aren't playing right now.")
            return
        try:
            self._voters.index(user)
            self._gmsg("You already voted, {}, give someone else a chance to "
                       "hate this question".format(user))
            return
        except:
            if self._votes < 2:
                self._votes += 1
                self._voters.append(user)
                print(self._voters)
                self._gmsg("{}, you have voted. {} more votes needed to "
                           "skip.".format(user, str(3 - self._votes)))
            else:
                self._votes = 0
                self._voters = []
                self._next_question(None, None, None)

    def _start(self, args, user, channel):
        '''
        Starts the trivia game.

        TODO: Load scores from last game, if any.
        '''
        if self._lc.running:
            return
        else:
            self._gmsg("Starting a new round!")
            self._lc.start(config.WAIT_INTERVAL)
            self.factory.running = True

    def _stop(self, *args):
        '''
        Stops the game and thanks people for playing,
        then saves the scores.
        '''
        if not self._lc.running:
            return
        else:
            self._lc.stop()
            self._round_question_num = 0
            self._gmsg('Thanks for playing!')
            #self._gmsg('Current rankings were:')
            self._standings(None, self._game_channel, self._game_channel)
            self._gmsg('''Scores have been saved, and see you next game!''')
            self._save_game()
            self.factory.running = False


            # we should reset all scores so it's clean for the next one
            # create a date





    def _save_game(self, *args):
        '''
        Saves the game to the data directory.
        '''
        with open(os.path.join(config.SAVE_DIR, 'scores.json'), 'w') as savefile:
            json.dump(self._scores, savefile)
            print("Scores have been saved.")

        with open(os.path.join(config.SAVE_DIR, 'teams.json'), 'w') as savefile:
            json.dump(self._teams, savefile)
            print("Teams have been saved.")

    def _load_game(self):
        '''
        Loads the running data from previous games.
        '''
        # ensure initialization
        self._scores = {}
        if not path.exists(config.SAVE_DIR):
            print("Save directory doesn't exist.")
            return
        try:
            with open(os.path.join(config.SAVE_DIR, 'scores.json'), 'r') as savefile:
                self._scores = json.load(savefile)
        except:
            print("Save file doesn't exist.")
            return

        if 'user' not in self._scores:
          self._scores['user'] = {}
        if 'team' not in self._scores:
          self._scores['team'] = {}

        print(self._scores)
        print("Scores loaded.")

        # ensure initialization
        self._teams = {}
        if not path.exists(config.SAVE_DIR):
            print("Save directory doesn't exist.")
            return
        try:
            with open(os.path.join(config.SAVE_DIR, 'teams.json'), 'r') as savefile:
                temp_dict = json.load(savefile)
        except:
            print("Team file doesn't exist.")
            return

        self._teams = temp_dict
        print(self._scores)
        print("Teams loaded.")



    def _set_user_score(self, args, user, channel):
        '''
        Administrative action taken to adjust scores, if needed.
        '''
        try:
            self._scores[args[0]] = int(args[1])
        except:
            self._cmsg(user, args[0] + " not in scores database.")
            return
        self._cmsg(user, args[0] + " score set to " + args[1])

    def _die(self, *args):
        '''
        Terminates execution of the bot.
        '''
        self._quit = True
        self.quit(message='This is triviabot, signing off.')

    def _restart(self, *args):
        '''
        Restarts the bot
        '''
        self._restarting = True
        print('Restarting')
        self.quit(message='Triviabot restarting.')

    def connectionLost(self, reason):
        '''
        Called when connection is lost
        '''
        global reactor
        if self._restarting:
            try:
                execl(sys.executable, *([sys.executable]+sys.argv))
            except Exception as e:
                print("Failed to restart: {}".format(e))
        if self._quit:
            reactor.stop()

    def _score(self, args, user, channel):
        '''
        Tells the user their score.
        '''
        try:
            self._cmsg(user, "Your current score is: {}"
                       .format(str(self._scores[user])))
        except:
            self._cmsg(user, "You aren't in my database.")

    def _next_question(self, args, user, channel):
        '''
        Administratively skips the current question.
        '''
        if not self._lc.running:
            self._gmsg("We are not playing right now.")
            return
        self._gmsg("Question has been skipped. The answer was: {}".format(self._answer.answer))
        self._clue_number = 0
        self._lc.stop()
        self._lc.start(config.WAIT_INTERVAL)

    def _reset(self, args, user, channel):

        fn = "scores-{:%Y-%m-%d-%H:%M:%S}.json".format(datetime.datetime.now())
        with open(os.path.join(config.SAVE_DIR, fn), 'w') as savefile:
            json.dump(self._scores, savefile)
            self._scores = {}
            self._scores['user'] = {}
            self._scores['team'] = {}
            print("Scores have been reset.")


    def _standings(self, args, user, channel):
        '''
        Tells the user the complete standings in the game.

        TODO: order them.
        '''

        dst = user
        if not channel == self.nickname:
          dst = channel

        self._cmsg(user, "The current trivia standings are: ")
        sorted_scores = sorted(self._scores['team'].iteritems(), key=lambda (k, v): (v, k), reverse=True)
        for rank, (player, score) in enumerate(sorted_scores, start=1):
            formatted_score = "{}: {}: {}".format(rank, player, score)
            self._cmsg(dst, formatted_score)

    def _give_clue(self, args, user, channel):
        if not self._lc.running:
            self._gmsg("we are not playing right now.")
            return
        qn = self._round_question_num
        self._round_question_num += 1
        self._cmsg(channel, "Question [{}/{}]: ".format(qn, self.round_questions))
        self._cmsg(channel, self._question)
        self._cmsg(channel, "Clue: " + self._answer.current_clue())

    def _get_new_question(self):
        '''
        Selects a new question from the questions directory and
        sets it.
        '''
        damaged_question = True
        while damaged_question:
            # randomly select file
            filename = choice(listdir(self._questions_dir))
            fd = open(os.path.join(config.Q_DIR, filename))
            lines = fd.read().splitlines()
            myline = choice(lines)
            fd.close()
            try:
                self._question, temp_answer = myline.split('`')
            except ValueError:
                print("Broken question:")
                print(myline)
                continue
            self._answer.set_answer(temp_answer.strip())
            damaged_question = False


class ircbotFactory(ClientFactory):
    protocol = triviabot

    def __init__(self, nickname=config.DEFAULT_NICK):
        self.nickname = nickname
        self.running = False
        self.lineRate = config.LINE_RATE

    def clientConnectionLost(self, connector, reason):
        print("Lost connection ({})".format(reason))
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        print("Could not connect: {}".format(reason))
        connector.connect()


if __name__ == "__main__":
    # SSL will be attempted in all cases unless "NO" is explicity specified
    # in the config
    if config.USE_SSL.lower() == "no":
        reactor.connectTCP(config.SERVER, config.SERVER_PORT, ircbotFactory())
    else:
        reactor.connectSSL(config.SERVER, config.SERVER_PORT,
                           ircbotFactory(), ssl.ClientContextFactory())

    reactor.run()
