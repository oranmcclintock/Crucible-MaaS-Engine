import argparse
import sys
import os

from profiler import run_profile
from codegen import generate_all
from executor import run_migration

def main():
    parser = argparse.ArgumentParser(description="Legacy Migration Pipeline Wrapper")
    parser.add_argument("--db-url", type=str, default="sqlite:///legacy_source.db",
                        help="Source database URL (SQLAlchemy format). E.g. postgres://user:pass@host/db")
    parser.add_argument("--profile-out", type=str, default="profile.json",
                        help="Path to save the profile output")
    parser.add_argument("--codegen-dir", type=str, default="generated",
                        help="Directory to save generated python modules")
    parser.add_argument("--skip-profiler", action="store_true", help="Skip the profiling phase")
    parser.add_argument("--skip-codegen", action="store_true", help="Skip the code generation phase")
    parser.add_argument("--skip-executor", action="store_true", help="Skip the execution phase")
    parser.add_argument("--auto-approve", action="store_true", help="Do not pause for manual review of generated code")

    args = parser.parse_args()

    print("=== Automated Legacy Migration Pipeline ===")
    print(f"Target DB: {args.db_url}\n")

    # Phase 1: Profiler
    if not args.skip_profiler:
        print("-> Phase 1: Profiling legacy schema...")
        p = run_profile(args.db_url, out_path=args.profile_out)
        print(f"Profile saved to {args.profile_out}")
    else:
        print("-> Phase 1: Skipped")

    # Phase 2: Codegen
    if not args.skip_codegen:
        print("\n-> Phase 2: Generating transformation modules...")
        files = generate_all(profile_path=args.profile_out, out_dir=args.codegen_dir)
        print("Generated modules:")
        for f in files:
            print(f"  {f}")
        
        if not args.auto_approve and not args.skip_executor:
            print("\n[!] GENERATED CODE REVIEW")
            print("Please review the generated code in the above files before continuing.")
            resp = input("Type 'yes' to approve and execute, or anything else to abort: ")
            if resp.lower().strip() != 'yes':
                print("Pipeline aborted by user.")
                sys.exit(1)
    else:
        print("\n-> Phase 2: Skipped")

    # Phase 3: Executor
    if not args.skip_executor:
        print("\n-> Phase 3: Executing sandboxed pipeline...")
        run_migration(engine_url=args.db_url, profile_path=args.profile_out)
        print("\nPipeline execution complete. Check quarantine/quarantine.db for rejected rows.")
    else:
        print("\n-> Phase 3: Skipped")


if __name__ == "__main__":
    main()
