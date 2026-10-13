from lab.common import decode_token, issue_token


def new_run(prompt, owner="alice", discovery="lazy"):
    from lab.state import create_run

    return create_run(
        prompt, decode_token(issue_token(owner)), mode="mock", discovery=discovery
    )
