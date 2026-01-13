"""Entry point for alamode CLI."""

import argparse
import logging

from claude_alamode.app import ChatApp
from claude_alamode.sessions import get_recent_sessions

# Set up file logging
logging.basicConfig(
    filename="alamode.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Claude à la Mode")
    parser.add_argument(
        "--resume", "-r", action="store_true", help="Resume the most recent session"
    )
    parser.add_argument("--session", "-s", type=str, help="Resume a specific session ID")
    parser.add_argument("prompt", nargs="*", help="Initial prompt to send")
    args = parser.parse_args()

    initial_prompt = " ".join(args.prompt) if args.prompt else None

    resume_id = None
    if args.session:
        resume_id = args.session
    elif args.resume:
        sessions = get_recent_sessions(limit=1)
        if sessions:
            resume_id = sessions[0][0]

    try:
        app = ChatApp(resume_session_id=resume_id, initial_prompt=initial_prompt)
        app.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        import traceback

        with open("/tmp/alamode-crash.log", "w") as f:
            traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
