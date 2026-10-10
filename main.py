import sys
from urllib.parse import parse_qsl

from lib import ui


def run():
    params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    ui.route(params)


if __name__ == '__main__':
    run()
