import argparse


def main():
    p = argparse.ArgumentParser(prog="dgca")
    p.add_subparsers(dest="cmd", required=True)  # crawl / parse / load / app land in M1+
    p.parse_args()
