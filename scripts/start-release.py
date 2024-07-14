#!/usr/bin/env python3

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile


AWS_PROFILE = "rust-start-release"
AWS_REGION = "us-west-1"
AWS_SSO_ACCOUNT_ID = "890664054962"
AWS_SSO_REGION = "us-east-1"
AWS_SSO_ROLE_NAME = "StartRelease"
AWS_SSO_SESSION = "rust-lang"


def parse_args():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)

    # Actions without arguments:
    commands.add_parser("update-rust-branches")
    commands.add_parser("publish-rust-dev-nightly")
    commands.add_parser("publish-rust-dev-beta")
    commands.add_parser("publish-rust-dev-stable-rebuild")
    commands.add_parser("publish-rust-prod-stable")

    # Actions with arguments:
    dev_stable = commands.add_parser("publish-rust-dev-stable")
    dev_stable.add_argument("date")

    # The result of the command line parsing is turned into a dict that will be
    # passed as-is as the lambda payload. Ensure the dest names of the parser
    # match what the lambda expects.
    return vars(parser.parse_args())


def invoke_lambda(name, payload):
    progress("starting the release process (might take a few minutes to begin)")

    with tempfile.TemporaryDirectory() as output_dir:
        output_file = os.path.join(output_dir, "out")
        subprocess.run(
            [
                "aws",
                "--profile",
                AWS_PROFILE,
                "lambda",
                "invoke",
                "--function-name",
                name,
                "--payload",
                base64.b64encode(json.dumps(payload).encode("utf-8")),
                output_file,
                # The lambda has an execution limit of 15 minutes, as it
                # constantly pools for the CodeBuild job to actually start
                # before responding to the client. The build starting might
                # take more than the default read timeout of 1 minute, and if
                # we don't increase it the invocation will fail.
                #
                # Worse, if we didn't cap the tries to 1 (see below) this would
                # result in the lambda (and thus the release) being invoked
                # multiple times when builds take a while to start.
                "--cli-read-timeout=900",  # 15 minutes
            ],
            env={
                **os.environ,
                # By default the AWS CLI retries the API call multiple times if
                # there is a failure (for example a timeout if the lambda takes
                # too much time to execute). This is **BAD** for this command,
                # as it would result in multiple release processes being queued
                # at the same time. Ensure only one attempt is made.
                "AWS_MAX_ATTEMPTS": "1",
            },
            check=True,
        )

        output = json.load(open(output_file))

        eprint("")
        eprint("The CodeBuild job running the release just started!")
        eprint("You can follow the logs online at:")
        eprint("")
        eprint(f"    {output['logs_link']}")
        eprint("")
        eprint("You can follow the logs on the CLI by running:")
        eprint("")
        eprint(
            f"    aws --profile {AWS_PROFILE} logs tail {output['logs_group']} --follow"
        )
        eprint("")
        eprint("You can cancel the build with:")
        eprint("")
        eprint(
            f"    aws --profile {AWS_PROFILE} codebuild stop-build --id \"{output['build_id']}\" --query build.buildStatus"
        )
        eprint("")


# Ensure at least version 2.9 of the AWS CLI is present, as it is the first
# one supporting sso-session.
def ensure_aws_cli():
    progress("ensuring the correct version of the AWS CLI is present")
    try:
        version = subprocess.run(
            ["aws", "--version"], check=True, text=True, stdout=subprocess.PIPE
        ).stdout
    except FileNotFoundError:
        error("the aws command doesn't seem to be installed")
    if not version.startswith("aws-cli/2.") or int(version.split(".")[1]) < 9:
        error("the aws command doesn't seem to be the AWS CLI version >= 2.9")


# Ensure the CLI is properly configured to authenticate with AWS SSO.
def ensure_aws_profile():
    progress("ensuring the AWS CLI is properly configured")

    missing = {"sso-session", "profile"}
    try:
        lines = open(os.path.expanduser("~/.aws/config")).read().split("\n")
        if "[sso-session rust-lang]" in lines:
            missing.remove("sso-session")
        if "[profile rust-start-release]" in lines:
            missing.remove("profile")
    except FileNotFoundError:
        pass

    if not missing:
        return

    eprint("error: you need to add the following snippets to ~/.aws/config")
    if "sso-session" in missing:
        print()
        print(f"[sso-session {AWS_SSO_SESSION}]")
        print("sso_region = us-east-1")
        print("sso_start_url = https://rust-lang.awsapps.com/start")
    if "profile" in missing:
        print()
        print(f"[profile {AWS_PROFILE}]")
        print(f"sso_session = {AWS_SSO_SESSION}")
        print(f"sso_account_id = {AWS_SSO_ACCOUNT_ID}")
        print(f"sso_role_name = {AWS_SSO_ROLE_NAME}")
        print(f"region = {AWS_REGION}")

    exit(1)


# Ensure you are authenticated with AWS SSO.
def ensure_aws_sso_session():
    progress("ensuring you are authenticated with AWS")

    output = subprocess.run(
        ["aws", "--profile", AWS_PROFILE, "sts", "get-caller-identity"],
    )
    if output.returncode == 0:
        return

    eprint("error: you are not authenticated with AWS SSO, run this command:")
    eprint("")
    eprint(f"    aws sso login --profile {AWS_PROFILE}")
    eprint("")
    exit(1)


def main():
    payload = parse_args()

    ensure_aws_cli()
    ensure_aws_profile()
    ensure_aws_sso_session()

    invoke_lambda("start-release", payload)


def progress(message):
    eprint(f"===> {message}")


def error(message):
    eprint(f"error: {message}")
    exit(1)


def eprint(message):
    print(message, file=sys.stderr)


if __name__ == "__main__":
    main()
