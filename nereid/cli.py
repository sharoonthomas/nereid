# This file is part of Tryton & Nereid. The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import sys

import click


@click.group()
def cli():
    pass


@cli.command()
def createdb():
    click.echo('creating new database')


def main(as_module=False):
    this_module = __package__ + '.cli'
    args = sys.argv[1:]

    if as_module:
        if sys.version_info >= (2, 7):
            name = 'python -m ' + this_module.rsplit('.', 1)[0]
        else:
            name = 'python -m ' + this_module

        # This module is always executed as "python -m flask.run" and as such
        # we need to ensure that we restore the actual command line so that
        # the reloader can properly operate.
        sys.argv = ['-m', this_module] + sys.argv[1:]
    else:
        name = None

    cli.main(args=args, prog_name=name, obj={},
             auto_envvar_prefix='NEREID')


if __name__ == '__main__':
    main(as_module=True)
