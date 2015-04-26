#!/usr/bin/env python
import argparse
import operator
import os

import json
import requests
import re
from jira import JIRA

requests.packages.urllib3.disable_warnings()

PROJECT_ID = "10500"
ISSUE_TYPE = "Test Case"
EXECUTION_MODE = "Automatic"
AUTOMATION_CANDIDATE = "Unknown"
TEST_PRIORITY = "High"
TEST_REVIEWED = "Yes"
LINK_TYPE = "is tested by"


def parse_command_line():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--data', required=True, dest='data')
    parser.add_argument('-p', '--project', required=True, dest='project')
    parser.add_argument('--pdihub-user', required=True, dest='pdihub_user')
    parser.add_argument('--pdihub-password', required=True, dest='pdihub_pass')
    parser.add_argument('--jira-user', required=True, dest='jira_user')
    parser.add_argument('--jira-password', required=True, dest='jira_pass',
                        default=os.getenv('JIRA_PASS'))
    parser.add_argument('-s', '--server', dest='server_jira',
                        default='https://jirapdi.tid.es')
    return parser.parse_args()


def should_be_handled(args):
    try:
        state_pr = args.data["pull_request"]["state"]
        merged_pr = args.data["pull_request"]["merged"]
    except:
        return False

    if state_pr == "closed" and merged_pr:
        return True
    else:
        return False


def parse_jira_id(args):
    branch_from = args.data["pull_request"]["head"]["ref"]
    pattern = re.compile(r"(?P<jira_id>{0}-[0-9]+)".format(args.project))
    match = pattern.search(branch_from.upper())

    return match.group('jira_id') if match else branch_from


def create_jira_client(server_jira, jira_user, jira_pass):
    options = {
        'server': server_jira,
        'verify': False,  # Avoid validating the SSL certificate.
                          # In jirapdi we trust
        'check_update': False  # Avoid going to pypi to check
                               # if there's a new JIRA client version
    }
    return JIRA(options=options, basic_auth=(jira_user, jira_pass))


def parse_test(test_case, testcase_parts):
    test_filtered = {}
    index_part = {}

    for part in testcase_parts:
        index_part[part] = [test_case.index(line) for line in test_case if part in line]

    test_filtered_sorted = sorted(index_part.iteritems(), key=operator.itemgetter(1))

    for i in range(len(test_filtered_sorted)):
        part = test_filtered_sorted[i][0]
        if i != len(test_filtered_sorted)-1:
            test_filtered[part] = test_case[test_filtered_sorted[i][1][0]:test_filtered_sorted[i+1][1][0]]
        else:
            test_filtered[part] = test_case[test_filtered_sorted[i][1][0]:]

    return test_filtered


class JiraInteraction(object):

    def __init__(self, args):
        self.client = create_jira_client(args.server_jira, args.jira_user,
                                         args.jira_pass)
        self.args = args

    def exist_testcase(self, name_testcase, jira_id):
        summary_link, key_link = self.check_userstory(jira_id)
        dict_userstory = {}
        dict_userstory = dict(zip(summary_link, key_link))

        return (True, dict_userstory[name_testcase]) if name_testcase in summary_link else (False, '')

    def check_userstory(self, jira_id):
        issue_jira = self.client.issue(jira_id)

        issue_links = issue_jira.fields.issuelinks
        summary_link = []
        key_link = []

        for link in issue_links:
            summary_link.append(link.outwardIssue.fields.summary)
            key_link.append(link.outwardIssue.key)

    return summary_link, key_link

    def testcase_to_jira(self, jira_id, info_file, content):
        scenarios = info_file.split('Scenario Outline:')
        template = {
            "name_testcase": "",
            "pre_requisite": "",
            "procedure": "",
            "expected": "",
            "dataset": "",
            "all_test": "",
            "feature": scenarios[0],
        }

        testcase_parts = ['Given', 'When', 'Then', 'Examples']

        for test in scenarios[1:]:
            test_case = test.splitlines()
            template["all_test"] = test
            template["name_testcase"] = test.splitlines()[0]
            check_testcase = self.exist_testcase(template["name_testcase"], jira_id)
            dict_test = parse_test(test_case, testcase_parts)

            template["pre_requisite"] = str("\r\n".join(dict_test['Given']))
            template["procedure"] = str("\r\n".join(dict_test['When']))
            template["expected"] = str("\r\n".join(dict_test['Then']))
            template["dataset"] = str("\r\n".join(dict_test['Examples']))

            if not check_testcase:
                self.request_to_jira(jira_id, info_file, content, self.args, template,
                                     self.client)
            else:
                self.update_jira(check_testcase[1], template, self.args, self.client)

    def update_jira(self, jira_test_case, test_info):
        update_template = {
            "customfield_10070": test_info["pre_requisite"],
            "customfield_10071": test_info["procedure"],
            "customfield_10072": test_info["expected"],
            "customfield_10153": test_info["dataset"],
            }

        update_issue = self.client.issue(jira_test_case)
        update_issue.update(fields=update_template)

    def request_to_jira(self, jira_id, info_file, content, test_info):
        issue_template = {
            "project": {"id": PROJECT_ID, "name": self.args.project},
            "summary": test_info["name_testcase"],
            "description": test_info["feature"],
            "issuetype": {"name": ISSUE_TYPE},
            "customfield_10150": {"value": EXECUTION_MODE},
            "customfield_10161": {"value": AUTOMATION_CANDIDATE},
            "customfield_10152": {"value": TEST_PRIORITY},
            "customfield_10162": {"value": TEST_REVIEWED},
            "customfield_10070": test_info["pre_requisite"],
            "customfield_10071": test_info["procedure"],
            "customfield_10072": test_info["expected"],
            "customfield_10153": test_info["dataset"],
            }

        print json.dumps(issue_template, indent=4)

        new_issue = self.client.create_issue(fields=issue_template)
        new_jira_id = new_issue.key

        self.client.create_issue_link(
            type=LINK_TYPE,
            inwardIssue=jira_id,
            outwardIssue=new_jira_id
            )

    def post_files(self, output_commit, auth, jira_id, content):
        for raws in output_commit.json()["files"]:
            raw_file = raws["raw_url"]
            pattern = re.compile(r".+.feature")
            raw_filename = raws["filename"]
            raw_status = raws["status"]

            info_file = requests.get(raw_file, auth=auth, verify=False).text if pattern.search(raw_filename) else None

            print "\n- The file {0} was {1}".format(raw_filename, raw_status)
            print "\n" + info_file if info_file else "This file is not a Test"

        self.testcase_to_jira(jira_id, info_file, content)


def undermine_payload(args):
    content = {
        'title_pr': args.data["pull_request"]["title"],
        'user_pr': args.data["pull_request"]["user"]["login"],
        'date_merged': args.data["pull_request"]["merged_at"]
    }
    return content


def run():
    args = parse_command_line()
    args.data = json.loads(args.data)

    if should_be_handled(args):
        sha = args.data["pull_request"]["head"]["sha"]
        commit_url = re.sub(r'{/sha}$', '/' + sha, args.data["repository"]["commits_url"])
        auth = (args.pdihub_user, args.pdihub_pass)
        output_commit = requests.get(commit_url, auth=auth, verify=False)
        jira_id = parse_jira_id(args)
        payload = undermine_payload(args)
        print "\n_____ The TestCase {0} was created at {1} _____".format(jira_id, args.data["pull_request"]["merged_at"])
        JiraInteraction(args).post_files(output_commit, auth, jira_id, payload)

if __name__ == "__main__":
    run()
