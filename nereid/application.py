#This file is part of Tryton & Nereid. The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.

from __future__ import with_statement

import os  # noqa
import warnings

from flask import Flask
from flask.config import ConfigAttribute
from flask.globals import _request_ctx_stack
from flask.helpers import locked_cached_property
from jinja2 import MemcachedBytecodeCache
from werkzeug import import_string

from trytond import backend
from trytond.pool import Pool
from trytond.cache import Cache
from trytond.config import CONFIG
from trytond.modules import register_classes
from trytond.transaction import Transaction

from .wrappers import Request, Response
from .session import NereidSessionInterface
from .templating import nereid_default_template_ctx_processor, \
    NEREID_TEMPLATE_FILTERS, ModuleTemplateLoader, LazyRenderer
from .helpers import url_for, root_transaction_if_required
from .ctx import RequestContext
from .signals import transaction_start, transaction_stop


class Nereid(Flask):
    """
    ...

    Unlike typical web frameworks and their APIs, nereid depends more on
    configuration and not direct python modules written along the APIs
    Most of the functional code will remain on the modules installed on
    Tryton, and the database configurations.

    ...

    """
    #: The class that is used for request objects.  See
    #: :class:`~nereid.wrappers.Request`
    #: for more information.
    request_class = Request

    #: The class that is used for response objects.  See
    #: :class:`~nereid.wrappers.Response` for more information.
    response_class = Response

    #: the session interface to use.  By default an instance of
    #: :class:`~nereid.session.NereidSessionInterface` is used here.
    session_interface = NereidSessionInterface()

    #: An internal attribute to hold the Tryton model pool to avoid being
    #: initialised at every request as it is quite expensive to do so.
    #: To access the pool from modules, use the :meth:`pool`
    _pool = None

    #: The attribute holds a connection to the database backend.
    _database = None

    #: Configuration file for Tryton. The path to the configuration file
    #: can be specified and will be loaded when the application is
    #: initialised
    tryton_configfile = ConfigAttribute('TRYTON_CONFIG')

    #: The location where the translations of the template are stored
    translations_path = ConfigAttribute('TRANSLATIONS_PATH')

    #: The name of the database to connect to on initialisation
    database_name = ConfigAttribute('DATABASE_NAME')

    #: The default timeout to use if the timeout is not explicitly
    #: specified in the set or set many argument
    cache_default_timeout = ConfigAttribute('CACHE_DEFAULT_TIMEOUT')

    #: the maximum number of items the cache stores before it starts
    #: deleting some items.
    #: Applies for: SimpleCache, FileSystemCache
    cache_threshold = ConfigAttribute('CACHE_THRESHOLD')

    #: a prefix that is added before all keys. This makes it possible
    #: to use the same memcached server for different applications.
    #: Applies for: MecachedCache, GAEMemcachedCache
    #: If key_prefix is none the value of site is used as key
    cache_key_prefix = ConfigAttribute('CACHE_KEY_PREFIX')

    #: a list or tuple of server addresses or alternatively a
    #: `memcache.Client` or a compatible client.
    cache_memcached_servers = ConfigAttribute('CACHE_MEMCACHED_SERVERS')

    #: The directory where cache files are stored if FileSystemCache is used
    cache_dir = ConfigAttribute('CACHE_DIR')

    #: The type of cache to use. The type must be a full specification of
    #: the module so that an import can be made. Examples for werkzeug
    #: backends are given below
    #:
    #:  NullCache - werkzeug.contrib.cache.NullCache (default)
    #:  SimpleCache - werkzeug.contrib.cache.SimpleCache
    #:  MemcachedCache - werkzeug.contrib.cache.MemcachedCache
    #:  GAEMemcachedCache -  werkzeug.contrib.cache.GAEMemcachedCache
    #:  FileSystemCache - werkzeug.contrib.cache.FileSystemCache
    cache_type = ConfigAttribute('CACHE_TYPE')

    #: If a custom cache backend unknown to Nereid is used, then
    #: the arguments that are needed for the initialisation
    #: of the cache could be passed here as a `dict`
    cache_init_kwargs = ConfigAttribute('CACHE_INIT_KWARGS')

    #: boolean attribute to indicate if the initialisation of backend
    #: connection and other nereid support features are loaded. The
    #: application can work only after the initialisation is done.
    #: It is not advisable to set this manually, instead call the
    #: :meth:`initialise`
    initialised = False

    #: Prefix the name of the website to the template name sutomatically
    #: This feature would be deprecated in future in lieu of writing
    #: Jinja2 Loaders which could offer this behavior. This is set to False
    #: by default. For backward compatibility of loading templates from
    #: a template folder which has website names as subfolders, set this
    #: to True
    #:
    #: .. versionadded:: 2.8.0.4
    template_prefix_website_name = ConfigAttribute(
        'TEMPLATE_PREFIX_WEBSITE_NAME'
    )

    def __init__(self, **config):
        """
        The import_name is forced into `Nereid`
        """
        super(Nereid, self).__init__('nereid', **config)

        # Update the defaults for config attributes introduced by nereid
        self.config.update({
            'TRYTON_CONFIG': None,
            'TEMPLATE_PREFIX_WEBSITE_NAME': True,

            'CACHE_TYPE': 'werkzeug.contrib.cache.NullCache',
            'CACHE_DEFAULT_TIMEOUT': 300,
            'CACHE_THRESHOLD': 500,
            'CACHE_INIT_KWARGS': {},
            'CACHE_KEY_PREFIX': '',
        })

    def initialise(self):
        """
        The application needs initialisation to load the database
        connection etc. In previous versions this was done with the
        initialisation of the class in the __init__ method. This is
        now separated into this function.
        """
        #: Check if the secret key is defined, if not raise an
        #: exception since it is required
        assert self.secret_key, 'Secret Key is not defined in config'

        #: Load the cache
        self.load_cache()

        self.view_functions['static'] = self.send_static_file

        # Backend initialisation
        self.load_backend()

        self.add_ctx_processors_from_db()

        # Add the additional template context processors
        self.template_context_processors[None].append(
            nereid_default_template_ctx_processor
        )

        # Finally set the initialised attribute
        self.initialised = True

    def load_cache(self):
        """
        Load the cache and assign the Cache interface to
        """
        BackendClass = import_string(self.cache_type)

        if self.cache_type == 'werkzeug.contrib.cache.NullCache':
            self.cache = BackendClass(self.cache_default_timeout)
        elif self.cache_type == 'werkzeug.contrib.cache.SimpleCache':
            self.cache = BackendClass(
                self.cache_threshold, self.cache_default_timeout)
        elif self.cache_type == 'werkzeug.contrib.cache.MemcachedCache':
            self.cache = BackendClass(
                self.cache_memcached_servers,
                self.cache_default_timeout,
                self.cache_key_prefix)
        elif self.cache_type == 'werkzeug.contrib.cache.GAEMemcachedCache':
            self.cache = BackendClass(
                self.cache_default_timeout,
                self.cache_key_prefix)
        elif self.cache_type == 'werkzeug.contrib.cache.FileSystemCache':
            self.cache = BackendClass(
                self.cache_dir,
                self.cache_threshold,
                self.cache_default_timeout)
        else:
            self.cache = BackendClass(**self.cache_init_kwargs)

    def load_backend(self):
        """
        This method loads the configuration file if specified and
        also connects to the backend, initialising the pool on the go
        """
        if self.tryton_configfile is not None:
            warnings.warn(DeprecationWarning(
                'TRYTON_CONFIG configuration will be deprecated in future.'
            ))
            CONFIG.update_etc(self.tryton_configfile)

        CONFIG.set_timezone()

        register_classes()

        # Load and initialise pool
        Database = backend.get('Database')
        self._database = Database(self.database_name).connect()
        self._pool = Pool(self.database_name)
        self._pool.init()

    @property
    def pool(self):
        """
        A proxy to the _pool
        """
        return self._pool

    @property
    def database(self):
        """
        Return connection to Database backend of tryton
        """
        return self._database

    @root_transaction_if_required
    def add_ctx_processors_from_db(self):
        """
        Adds template context processors registers with the model
        nereid.template.context_processor
        """
        ctx_processor_obj = self.pool.get('nereid.template.context_processor')

        db_ctx_processors = ctx_processor_obj.get_processors()
        if None in db_ctx_processors:
            self.template_context_processors[None].extend(
                db_ctx_processors.pop(None)
            )
        self.template_context_processors.update(db_ctx_processors)

    def request_context(self, environ):
        return RequestContext(self, environ)

    @root_transaction_if_required
    def create_url_adapter(self, request):
        """Creates a URL adapter for the given request.  The URL adapter
        is created at a point where the request context is not yet set up
        so the request is passed explicitly.

        """
        if request is not None:

            Website = Pool().get('nereid.website')

            website = Website.get_from_host(request.host)
            rv = website.get_url_adapter(self).bind_to_environ(
                request.environ,
                server_name=self.config['SERVER_NAME']
            )
            return rv

    def dispatch_request(self):
        """
        Does the request dispatching.  Matches the URL and returns the
        return value of the view or error handler.  This does not have to
        be a response object.
        """
        DatabaseOperationalError = backend.get('DatabaseOperationalError')

        req = _request_ctx_stack.top.request
        if req.routing_exception is not None:
            self.raise_routing_exception(req)

        rule = req.url_rule
        # if we provide automatic options for this URL and the
        # request came with the OPTIONS method, reply automatically
        if getattr(rule, 'provide_automatic_options', False) \
           and req.method == 'OPTIONS':
            return self.make_default_options_response()

        Cache.clean(self.database_name)

        with Transaction().start(self.database_name, 0, readonly=True):
            Website = Pool().get('nereid.website')
            website = Website.get_from_host(req.host)

            user, company = website.application_user.id, website.company.id

        for count in range(int(CONFIG['retry']), -1, -1):
            with Transaction().start(
                    self.database_name,
                    user, context={'company': company}) as txn:
                try:
                    transaction_start.send(self)
                    rv = self._dispatch_request(req)
                    txn.cursor.commit()
                except DatabaseOperationalError:
                    # Strict transaction handling may cause this.
                    # Rollback and Retry the whole transaction if within
                    # max retries, or raise exception and quit.
                    txn.cursor.rollback()
                    if count:
                        continue
                    raise
                except Exception:
                    # Rollback and raise any other exception
                    txn.cursor.rollback()
                    raise
                else:
                    return rv
                finally:
                    transaction_stop.send(self)

    def _dispatch_request(self, req):
        """
        Implement the nereid specific _dispatch
        """

        language = 'en_US'
        if req.nereid_website:
            # If this is a request specific to a website
            # then take the locale from the website
            language = req.nereid_locale.language.code

        with Transaction().set_context(language=language):

            # pop locale if specified in the view_args
            req.view_args.pop('locale', None)

            # otherwise dispatch to the handler for that endpoint
            if req.url_rule.endpoint in self.view_functions:
                meth = self.view_functions[req.url_rule.endpoint]
            else:
                model, method = req.url_rule.endpoint.rsplit('.', 1)
                meth = getattr(Pool().get(model), method)

            if not hasattr(meth, 'im_self') or meth.im_self:
                # static or class method
                result = meth(**req.view_args)
            else:
                # instance method, extract active_id from the url
                # arguments and pass the model instance as first argument
                model = Pool().get(req.url_rule.endpoint.rsplit('.', 1)[0])
                i = model(req.view_args.pop('active_id'))
                result = meth(i, **req.view_args)

            if isinstance(result, LazyRenderer):
                result = unicode(result)

            return result

    def create_jinja_environment(self):
        """
        Extend the default jinja environment that is created. Also
        the environment returned here should be specific to the current
        website.
        """
        rv = super(Nereid, self).create_jinja_environment()

        # Add the custom extensions specific to nereid
        rv.add_extension('jinja2.ext.i18n')
        rv.add_extension('nereid.templating.FragmentCacheExtension')

        rv.filters.update(**NEREID_TEMPLATE_FILTERS)

        # add the locale sensitive url_for of nereid
        rv.globals.update(url_for=url_for)

        if self.cache:
            # Setup the bytecode cache
            rv.bytecode_cache = MemcachedBytecodeCache(self.cache)
            # Setup for fragmented caching
            rv.fragment_cache = self.cache
            rv.fragment_cache_prefix = self.cache_key_prefix + "-frag-"

        return rv

    @locked_cached_property
    def jinja_loader(self):
        """
        Creates the loader for the Jinja2 Environment
        """
        return ModuleTemplateLoader(
            self.database_name, searchpath=self.template_folder,
        )

    def select_jinja_autoescape(self, filename):
        """
        Returns `True` if autoescaping should be active for the given
        template name.
        """
        if filename is None:
            return False
        if filename.endswith(('.jinja',)):
            return True
        return super(Nereid, self).select_jinja_autoescape(filename)

    @property
    def guest_user(self):
        warnings.warn(DeprecationWarning(
            "guest_user as an attribute will be deprecated.\n"
            "Use request.nereid_website.guest_user.id instead"
        ))
        from .globals import request
        return request.nereid_website.guest_user.id
