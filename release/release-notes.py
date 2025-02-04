#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2016
#

import sys
import argparse
import traceback
from libpagure import Pagure

from iparelease.gitinfo import GitInfo

WIKI_BLOB = """
{{ReleaseDate|%(release_date)s}}
The FreeIPA team would like to announce FreeIPA %(version)s release!

It can be downloaded from http://www.freeipa.org/page/Downloads. Builds for
Fedora distributions will be available from the official repository soon.

== Highlights in %(version)s ==

'''TODO RELEASE NOTES - put release notes (if any) to proper categories'''
%(release_notes)s
'''END TODO'''

=== Enhancements ===
=== Known Issues ===
%(known_issues)s

=== Bug fixes ===
FreeIPA %(version)s is a stabilization release for the features delivered as a
part of %(major_version)s version series.

There are more than %(num_bugs)s bug-fixes details of which can be seen in
the list of resolved tickets below.

== Upgrading ==
Upgrade instructions are available on [[Upgrade]] page.

== Feedback ==
Please provide comments, bugs and other feedback via the freeipa-users mailing
list (https://lists.fedoraproject.org/archives/list/freeipa-users@lists.fedorahosted.org/)
or #freeipa channel on libera.chat.

"""


GIT_DIR = None
PAGURE_REPO = "freeipa"


class OperationError(Exception):
    foo = ''


def query(
        pagure, status=None, tags=None, assignee=None, author=None,
        milestones=None
):
    issues = pagure.list_issues(
        status, tags, assignee, author, milestones=milestones)
    return issues

def normalize_val(val):
    """Pagure returns values in random casing, normalize to lowercase
    :param val: input value
    :return: None or lowercase string
    """
    if val is None:
        return val
    else:
        return val.lower()

def filter_by_close_status(tickets, statuses=()):
    # compare in lower case
    statuses = [s.lower() for s in statuses]
    matching = [
        t for t in tickets
        if normalize_val(t.get('close_status')) in statuses
    ]
    return matching


def filter_by_milestones(tickets, milestones=()):
    # compare in lower case
    milestones = [m.lower() for m in milestones]
    matching = [
        t for t in tickets
        if normalize_val(t.get('milestone')) in milestones]
    return matching


def get_custom_field(ticket, name, default=None):
    for field in ticket.get('custom_fields', ()):
        if field['name'].lower() == name.lower():
            return field['value']
    return default


def append_custom_field(ticket, name, value):
    for field in ticket.get('custom_fields', ()):
        if field['name'].lower() == name.lower():
            field['value'].append(value)
            return
    field = {}
    field['name'] = name
    field['value'] = value
    fields = ticket.get('custom_fields', ())
    fields.append(field)
    ticket['custom_fields'] = fields


class App(object):
    def __init__(self, args):
        self.args = args
        self.pagure_token = None

        if args.token:
            self.pagure_token = args.token
        elif args.token_file:
            with open(args.token_file, 'r') as f:
                self.pagure_token = f.read().strip()
        else:
            RuntimeError(
                "Please specify Pagure token"
            )

    def run(self):
        try:
            pagure = Pagure(
                pagure_repository=PAGURE_REPO,
                pagure_token=self.pagure_token
            )
        except TypeError:
            pagure = Pagure(
                repo_to=PAGURE_REPO,
                pagure_token=self.pagure_token
            )
        git = self._get_commits()

        if not self.args.nomilestones:
            # get all fixed tickets from the primary milestone
            primary_closed = query(
                pagure, "Closed", milestones=[self.args.milestone])
            primary_fixed = filter_by_close_status(primary_closed, [u'fixed'])

            # get all fixed tickets from other milestones that can be possibly
            # fixed in this release
            if self.args.milestones:
                possibly_closed = query(
                    pagure, "Closed", milestones=self.args.milestones)
                possibly_fixed = filter_by_close_status(
                    possibly_closed, [u'fixed'])
                fixed_here_tickets = self._filter_tickets(
                    possibly_fixed, git.commits) + primary_fixed
            else:
                fixed_here_tickets = primary_fixed
        else:
            fixed_here_tickets = []

        ticket_issues = list(set([issue.get('id') for issue in fixed_here_tickets]))
        for commit in git.commits:
            if len(commit.tickets) > 0:
                for issue in commit.tickets:
                    if issue not in ticket_issues:
                        fixed_here_tickets.append(pagure.issue_info(issue))
                        ticket_issues.append(issue)
                    if commit.release_note:
                        for t in fixed_here_tickets:
                            if str(t.get('id')) == issue:
                                append_custom_field(t, 'changelog', commit.release_note)
                                break

        fixed_tickets = {}
        for t in fixed_here_tickets:
            if t['id'] not in fixed_tickets:
                fixed_tickets[t['id']] = t

        sorted_tickets = sorted(fixed_tickets.values(), key=lambda x: x['id'])
        bugs = self._get_bugs(sorted_tickets)
        self._print_wiki(sorted_tickets, bugs, git)

    def _get_commits(self):
        global GIT_DIR
        git = GitInfo(GIT_DIR)
        log = git.get_log(self.args.revision_range)
        git.add_commits(log)
        return git

    def _get_bugs(self, tickets):
        bugs = []
        for ticket in tickets:
            # bugs migrated from Trac
            if normalize_val(get_custom_field(ticket, 'type')) == 'defect':
                bugs.append(ticket)
            # new pagure which doesn't have type
            elif 'rfe' not in ticket.get('tags'):
                bugs.append(ticket)
        return bugs


    def _filter_tickets(self, tickets, commits):
        commit_tickets = {}
        filtered = []
        for commit in commits:
            for t_id in commit.tickets:
                commit_tickets[t_id] = commit
        for ticket in tickets:
            if commit_tickets.get(str(ticket.get('id'))):
                filtered.append(ticket)

        return filtered

    def _release_notes_and_known_issues(self, tickets):
        release_notes = []
        known_issues = []
        for ticket in tickets:
            release_note = get_custom_field(ticket, 'changelog')
            knownissue = get_custom_field(ticket, 'knownissue', default='false')
            if isinstance(release_note, str):
                release_note = ':: ' + release_note
            if isinstance(release_note, list):
                release_note = ':: ' + ' '.join(release_note)
            if not release_note and '[RFE]' in ticket['title']:
                release_note = '\n'
            if release_note:
                release_note = "* {t[id]}: {t[title]}\n{changes}\n--------".format(
                        t=ticket, changes=release_note
                    )
                if knownissue != 'false':
                    known_issues.append(release_note)
                else:
                    release_notes.append(release_note)
        result = {'release_notes': "\n".join(release_notes),
                  'known_issues': "\n".join(known_issues)}
        return result

    def _print_wiki(self, tickets, bugs, git):
        bugs = len(bugs) - len(bugs) % 10 # get only estimate
        notes = self._release_notes_and_known_issues(tickets)

        wiki = WIKI_BLOB % dict(
            version=self.args.version,
            prev_version=self.args.prev_version,
            major_version=self.args.major_version,
            release_date=self.args.release_date,
            num_bugs=bugs,
            release_notes=notes['release_notes'],
            known_issues=notes['known_issues']
        )
        print(wiki)
        self._print_tickets_wiki(tickets)
        self._print_commits_wiki(git)

    def _print_tickets_wiki(self, tickets):
        print("== Resolved tickets ==")
        for ticket in tickets:
            num = ticket.get('id')
            summary = ticket.get('title')
            if self.args.links:
                print("* [https://pagure.io/freeipa/issue/%s #%s] %s" %
                    (num, num, summary))
            else:
                print("* %s %s" %(num, summary))

    def _print_commits_wiki(self, git):
        print("== Detailed changelog since %s ==" % self.args.prev_version)
        authors = list(git.authors.keys())
        authors.sort()
        for mail in authors:
            author = git.authors[mail]
            commits = author.commits
            if len(commits) < 1:
                continue
            print("=== %s (%d) ===" % (author.name, len(commits)))
            for c in commits:
               self._print_commit_wiki(c)
            print("")

    def _print_commit_wiki(self, commit):
        base = "* %s" % commit.summary.strip()
        if self.args.links:
            cgit = "[https://pagure.io/freeipa/c/%s commit]" % (
                commit.commit)
            tickets = []
            for t in sorted(list(commit.tickets)):
                tickets.append("[https://pagure.io/freeipa/issue/%s #%s]" % (t, t))
            tickets = ', '.join(tickets)
            print(base + " " + cgit + " " + tickets)
        else:
            print(base)


def parse_args():
    global GIT_DIR;
    desc = """
    Release notes \n \n

    python release-notes.py 4.4.2 2016-10-05 4.4.1 4.4.0 release-4-4-1..ipa-4-4 \
     "FreeIPA 4.4.2" -m  "Freeipa 4.2.4" "FreeIPA 4.2.5"  "FreeIPA 4.3.2" \
      --links
    """
    # create the top-level parser
    parser = argparse.ArgumentParser(
        prog='release-notes',
        description=desc)
    # parser.add_argument('-v', '--verbose', dest='v', action='count', default=0,
    #                     help='verbose output')

    parser.add_argument('version',  help='Released version')
    parser.add_argument('release_date', help='Released date')
    parser.add_argument('prev_version', help='Previous version')
    parser.add_argument('major_version', help='Major version')
    parser.add_argument('revision_range',
                        help='Revision range as in git log')
    parser.add_argument('milestone',
                        help='Primary milestone')
    parser.add_argument('--milestone', '-m', dest='milestones',
                        nargs='*', help='Additional milestones')
    parser.add_argument('--links', action="store_true",
                        help='With links to tickets and commits')
    parser.add_argument('--token', dest='token', action='store',
                        help='Pagure token for accessing issues',
                        metavar='TOKEN', default=None)
    parser.add_argument('--token-file', dest='token_file', action='store',
                        help='Path to file where pagure token is stored',
                        metavar='PATH', default=None)
    parser.add_argument('--repo', dest='git_repo', action='store',
                        help='Path to git repo to process',
                        metavar='GIT_DIR', default=GIT_DIR)
    parser.add_argument('--nomilestones', dest='nomilestones',
                        action="store_true",
                        help="Only use tickets mentioned in the commits")

    args = parser.parse_args()

    if not (args.token or args.token_file):
        raise RuntimeError(
            "Please specify --token or --token-file for pagure access")

    GIT_DIR = GIT_DIR or args.git_repo
    assert GIT_DIR, "Specify path to clean git repository with --repo"

    return args

if __name__ == '__main__':
    args = parse_args()
    try:
        app = App(args)
        app.run()
    except OperationError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print('***The command above has FAILED***')
        traceback.print_exc()
        sys.exit(1)
