#!env python
"""
Current issues:

 * Build docs and put them in gh-pages so that people can read them or something
 * Document -v switch
 * Warn if lessc isn't available
 * Determine how to wrap HTML nicely
   * Modify CSS, static element paths, leave everything else alone?
 * You can probably go into an infinite loop pretty easily using grab
 * Image resizing/conversion
 * Perhaps create a couple of intermediate classes for things like HTMLOutputFile or something
 * Blogs are shit.
   * Indexes, possibly with javascript to allow sorting / pagination


 ### Possible approach to HTML handling

 Allow the use of an option or _auto-html.jinja2 which can pull the <body> from an HTML file and replace all the headers
 with a standard set of stuff.

 ### Approach for blog handling

 An index.yml file in the root that allows the user to optionally "type" a directory. This could be used to assist in
 generating a blog, or a gallery or whatever.

 * Need to clean up code, particularly path handling within that area
 * Need to update blog templates to include
   * Next/Prev
   * Decent look
   * Date formatter
   * Maybe a sidebar or something
   * <-- more --> support?
   * Need to process blog before we do final render for the rest of the site so that, say, the index page can ref the
     blog posts - or could have a get_type that does the type processing for the blog first then hands it back

 WARNING/TODO: DO NOT FORGET YOU'RE USING A GIT VERSION OF CSSSELECT TO SUPPORT :first


 So, maybe it should be a two-phase operation. Phase one builds a tree of the source, with each element doing as much
 no-dependency processing as possible. The second phase then compiles the tree and writes it out.

"""
from datetime import datetime
import sys
import time

import os, re, jinja2, markdown2
import jinja2.ext
from path import path
from pyquery import PyQuery as pq
from optparse import OptionParser
import logging
import yaml

logging.basicConfig(level=logging.WARN)
log = logging.getLogger('statin')


class Markdown2Extension(jinja2.ext.Extension):
    """
    Jinja2 extension for Markdown, with a couple of modifications.

    We enable a few extra features, notably fenced code blocks, footnotes and header-ids.

    We also do some normalisation of lines before they enter the Markdown parser so that you don't have to have ugly
    indentation - if the markdown starts at indentation X, it'll treat that as the baseline so you can do:

    {% markdown %}
        My paragraph

         * foo
           * bar
    {% endmarkdown %}

    Without it freaking out.
    """
    tags = {'markdown'}

    def __init__(self, environment):
        super(Markdown2Extension, self).__init__(environment)
        environment.extend(
            markdowner=markdown2.Markdown(extras=['fenced-code-blocks','footnotes','header-ids'])
        )

    def parse(self, parser):
        line_number = parser.stream.next().lineno
        body = parser.parse_statements(
            ['name:endmarkdown'],
            drop_needle=True
        )
        return jinja2.nodes.CallBlock(
            self.call_method('_markdown_support'),
            [],
            [],
            body
        ).set_lineno(line_number)

    def normalise_lines(self, lines):
        """
        Take the first set of whitespace on the first line, and strip the remaining lines by that much whitespace.

        @param lines: Lines of markdown
        @type lines: str|unicode
        @return Normalised lines
        @rtype str|unicode

        """
        size = 0
        detected = False
        output = []
        for l in lines.split("\n"):
            if not detected:
                m = re.search(r'^( *)[^ ]', l)
                if m:
                    size = len(m.group(1))
                    detected = True
            if l[:size] == (" " * size):
                output.append(l[size:])
            else:
                # If the line doesn't start with the given number of spaces we assume 0 point instead
                output.append(l)

        return "\n".join(output)

    def _markdown_support(self, caller):
        markdown = self.normalise_lines(str(caller()))
        html = self.environment.markdowner.convert(markdown)
        return html



class NoHandlerFoundError(Exception):
    """
    Exception to be raised when a path is given that has no acceptable handler
    """
    pass


class Builder(object):
    """
    Manages the total build and relevant parameters
    """
    env = None

    def __init__(self, source_dir, dest_dir):
        """
        Initialise Builder
        @param source_dir:str Source directory
        @param dest_dir:str Destination directory
        """
        log.debug("Creating Builder from %s to %s" % (source_dir, dest_dir))
        self.env = BuildEnvironment(source_dir=source_dir, dest_dir=dest_dir)

    def register(self, handler):
        """
        Register a file handler class

        @param handler: File Handler
        @type handler: class
        """
        self.env.register(handler)

    def register_map(self, mapper):
        """
        Register a path mapper

        @param mapper: Path mapper
        @type mapper: class
        """
        self.env.register_map(mapper)

    def register_type(self, type_handler):
        """
        Register a directory type handler

        @param type_handler: Type handler
        @type type_handler: class
        """
        self.env.register_type(type_handler)

    def clean(self):
        """
        Clean out the destination directory
        """
        log.debug("Cleaning out destination dir")
        # We do it this way rather than just rmtree'ing the whole thing in order to ensure that reloaders can watch
        # the top-level dir without freaking out
        for p in self.env.dest_dir.listdir():
            if p.isdir():
                p.rmtree_p()
            else:
                p.remove()

    def build(self):
        """
        Build from source to destination
        """

        log.debug("Initiating build")
        self.env.dispatch_type(self.env.source_dir)


class BuildEnvironment(object):
    """
    Contains the environment and various helpers for Files
    """
    source_dir = None
    dest_dir = None
    handlers = None
    mappers = None
    jinja2_env = None
    type_handlers = None
    type_map = None

    def __init__(self, source_dir, dest_dir):
        """
        Initialise Build environment
        @param source_dir: Source directory
        @type source_dir: path
        @param dest_dir: Destination directory
        @type dest_dir: path
        """
        self.source_dir = path(source_dir).abspath()
        self.dest_dir = path(dest_dir).abspath()
        self.handlers = []
        self.mappers = []
        self.type_handlers = []
        self.type_map = dict()

    def register(self, handler):
        """
        Add a file handler
        @param handler: File handler
        @type handler: class
        """

        log.debug("Registering handler %r" % handler)
        self.handlers.append(handler(self))

    def register_map(self, mapper):
        """
        Add a path mapper

        @param mapper: Path mapper
        @type mapper: class
        """
        log.debug("Registering path mapper %r" % mapper)
        self.mappers.append(mapper(self))

    def register_type(self, type_handler):
        """
        Register a directory type handler

        @param type_handler: Type handler
        @type type_handler: class
        """
        log.debug("Registering type handler %r" % type_handler)
        self.type_handlers.append(type_handler(self))

    def dispatch_type(self, full_path):
        """
        Call handler for any given dir

        @param full_path: Path to directory
        @type full_path: path
        """
        meta = dict()
        yaml_path = full_path.joinpath('_index.yml')

        if yaml_path.exists():
            log.debug("Found an _index.yml in %s" % yaml_path)
            meta = yaml.load(open(yaml_path, 'r'), yaml.Loader)

        for t in self.type_handlers:
            if t.match(full_path, meta):
                self.type_map[full_path] = t.load(full_path, meta)
                log.debug("Registered type %r for path %s" % (self.type_map[full_path], full_path))
                self.type_map[full_path].process()
                return self.type_map[full_path]

    def get(self, file_path):
        """
        Retrieve a file from the given path via the matching handler.

        @param file_path: Absolute path to file
        @type file_path: path
        @return: File
        @rtype: BaseFile
        """

        log.debug("Looking for handler for %s" % file_path)

        for handler in self.handlers:
            if handler.match(file_path):
                log.debug("Found handler %r" % handler)
                return handler.load(file_path)

        raise NoHandlerFoundError(file_path)

    def map(self, file_path):
        """
        Convert a source path to a destination path

        @param file_path: Source path
        @type file_path: path
        @return Relative destination path
        @rtype str
        """

        file_path = self.source_dir.relpathto(file_path)

        log.debug("Looking for mapper for %s" % file_path)

        for mapper in self.mappers:
            if mapper.match(file_path):
                log.debug("Found mapper %r" % mapper)
                return mapper.relative(file_path)

    def to_dest(self, file_path):
        """
        Convert a source path to a destination path

        @param file_path: Source path
        @type file_path: path
        @return Destination path
        @rtype str
        """

        # Obtain the relative path from source to file, then add that to the destination
        return self.dest_dir.joinpath(self.map(file_path))


class BaseFileHandler(object):
    """
    Base class for matching and loading Files
    """
    def __init__(self, env):
        """
        Init the handler with the current environment

        @param env: Environment
        @type env: BuildEnvironment
        """
        self.env = env

    def match(self, file_path):
        """
        Match the path against the types this handler can process
        """
        raise NotImplementedError()

    def load(self, file_path):
        """
        Load a file from the given path
        """
        raise NotImplementedError()


class BaseFile(object):
    """
    Base class for the various File types and conversions
    """
    env = None
    handler = None

    def __init__(self, env, handler):
        """
        Init File representation

        @param env: Build environment
        @type env: BuildEnvironment
        @param handler: Handler for this file
        @type handler: BaseFileHandler
        """
        self.env = env
        self.handler = handler

    def read_from(self, file_path):
        """
        Read the source from the given absolute path

        @param file_path: Absolute path to source file
        @type file_path: path
        """

        raise NotImplementedError()

    def write_to(self, file_path):
        """
        Write the conversion of this file out to the given path

        @param file_path: Absolute path to write conversion out to
        @type file_path: path
        """
        raise NotImplementedError()

    def ensure_output_dir(self, file_path):
        """
        Ensure the dir for the given file path exists

        @param file_path: Path of file or dir
        @type file_path: path
        """

        # Ensure parent directory exists
        if not file_path.parent.isdir():
            file_path.parent.makedirs()


class AnyFileHandler(BaseFileHandler):
    """
    Handle any file at all, by simply copying it
    """
    def match(self, file_path):
        """
        This handler matches all files
        """
        return True
    
    def load(self, file_path):
        """
        Create the AnyFile from the path
        
        @param file_path: File to load
        @type file_path: path
        """
        af = AnyFile(self.env, self)
        af.read_from(file_path)
        return af


class AnyFile(BaseFile):
    """
    Represent any file at all by holding the original file path then copying on request.
    """
    def read_from(self, file_path):
        """
        "Read" the file from the given file_path

        @param file_path: Path to file to read
        @type file_path: path
        """
        self.file_path = file_path
    
    def write_to(self, file_path):
        """
        Write the file to the given file_path by copying from the read path. This will create any directories required
        to succeed.

        @param file_path: Path to write to
        @type file_path: path
        """

        self.ensure_output_dir(file_path)
        self.file_path.copy(file_path)


class Jinja2FileHandler(BaseFileHandler):
    """
    File handler for .jinja2 files
    """

    jinja2_env = None

    def __init__(self, env):
        """
            Set up Jinja2 environment if necessary

            @param env: Build Environment
            @type env: BuildEnvironment
        """
        super(Jinja2FileHandler, self).__init__(env)
        self.jinja2_env = jinja2.Environment(extensions=[Markdown2Extension],
                                             loader=jinja2.FileSystemLoader(self.env.source_dir))

        # Register various useful global functions
        self.jinja2_env.globals['grab'] = self.jinja2_grab
        self.jinja2_env.globals['select'] = self.jinja2_select
        self.jinja2_env.globals['glob'] = self.jinja2_glob
        self.jinja2_env.globals['map'] = self.env.map

    def match(self, file_path):
        """
        Can we handle this file type?
        @param file_path: File path
        @type file_path: path
        @return: True if we can handle it
        @rtype: bool
        """
        return file_path.ext == '.jinja2'

    def load(self, file_path):
        """
        Load given file path

        @param file_path: Path to jinja2 file
        @type file_path: path
        @return: Jinja2File object
        @rtype: Jinja2File
        """
        f = Jinja2File(self.env, self)
        f.read_from(file_path)
        return f

    def jinja2_grab(self, file_path):
        """
        Grab a source file

        @param file_path: Relative path to source file
        @type file_path: basestring|path
        @return: File object
        @rtype: BaseFile
        """
        return self.env.get(self.env.source_dir.joinpath(file_path))

    def jinja2_select(self, html, selector):
        """
        Perform a pyquery select on given HTML

        @param html: HTML string
        @type html: basestring
        @param selector: PyQuery Selector
        @type selector: basestring
        @return: Result of query
        """
        return pq(html)(selector)

    def jinja2_glob(self, pattern):
        """
        Return a list of matching source paths for a given pattern

        @param pattern: Pattern in glob format
        @type pattern: basestring
        @return: List of matching paths
        @rtype: list
        """
        return [self.env.source_dir.relpathto(p) for p in self.env.source_dir.glob(pattern)]



class Jinja2File(BaseFile):
    """
    Represent a Jinja2 file
    """

    def __init__(self, env, handler):
        """
        @param env: Build environment
        @type env: BuildEnvironment
        @param handler: Jinja2 file handler
        @type handler: Jinja2FileHandler
        """
        super(Jinja2File, self).__init__(env, handler)

    def read_from(self, file_path):
        """
        Read a Jinja2 file from the given path

        @param file_path: Jinja2 file path
        @type file_path: path
        """

        self.file_path = file_path
        self.template = self.handler.jinja2_env.get_template(str(self.env.source_dir.relpathto(file_path)))

    def write_to(self, file_path, **kwargs):
        """
        Write out a Jinja2 file to the given path

        @param file_path: Destination file path
        @type file_path: path
        """

        self.ensure_output_dir(file_path)
        open(file_path, 'w').write(self.template.render(source_path=self.file_path, destination_path=file_path, dispatch_type=self.jinja2_dispatch_type, path=path, url=self.env.map(self.file_path), to_root=self.jinja2_to_root(), **kwargs))

    def as_html(self, **kwargs):
        """
        Return jinja2 file as HTML (result of render)

        @return: HTML
        @rtype: basestring
        """
        return self.template.render(to_root=self.jinja2_to_root(), **kwargs)

    def jinja2_to_root(self):
        """
        Return the relative prefix to get to the root of the site
        TODO: This is not correct, it needs to use the path mappers to determine the end url relative to root

        @return: Relative path, ie ../../
        @rtype: str|unicode
        """
        return str(self.file_path.parent.relpathto(self.env.source_dir))

    def jinja2_dispatch_type(self, file_path):
        """
        Return a given type
        @param file_path: Source-relative path to type (dir)
        @type file_path: str|unicode
        """
        return self.env.dispatch_type(self.env.source_dir.joinpath(file_path))


class MarkdownFileHandler(BaseFileHandler):
    """
    Handle Markdown (.md) files
    """
    markdown = None

    def __init__(self, env):
        """
        Set up handler for Markdown files
        """
        super(MarkdownFileHandler, self).__init__(env)
        self.markdown = markdown2.Markdown(extras=['fenced-code-blocks', 'footnotes', 'header-ids'])

    def match(self, file_path):
        """
        Is this a markdown file?
        @param file_path: File path
        @type file_path: path
        @return: Is a Markdown file?
        @rtype: bool
        """
        return file_path.ext == '.md'

    def load(self, file_path):
        """
        Load Markdown file representation

        @param file_path: File path
        @type file_path: path
        @return: Markdown file representation
        @rtype: MarkdownFile
        """

        f = MarkdownFile(self.env, self)
        f.read_from(file_path)
        return f


class MarkdownFile(BaseFile):
    """
    Represent a Markdown file
    """

    def read_from(self, file_path):
        """
        Read the markdown file from the source dir
        @param file_path: File path to markdown file
        @type file_path: path
        """
        self.file_path = file_path

    def write_to(self, file_path):
        """
        Write out result of Markdown processing to given path, as HTML.

        @param file_path: Output file path
        @type file_path: path
        """

        self.ensure_output_dir(file_path)
        open(file_path, 'w').write(self.as_templated_html())

    def as_html(self):
        """
        Convert markdown to HTML (not templated, useful for parsing)

        @return: HTML
        @rtype: str|unicode
        """
        return self.handler.markdown.convert(open(self.file_path, 'r').read())

    def find_template(self):
        """
        Find the nearest template for md files path-wise

        @return: The template, or None
        @rtype: path|None
        """
        template_path = self.file_path
        while template_path != self.env.source_dir:
            template_path = template_path.parent
            log.debug("Looking for template for %s" % template_path.joinpath('_auto-md.jinja2'))
            if template_path.joinpath('_auto-md.jinja2').exists():
                log.debug("Found template for Markdown in %s" % template_path)
                return template_path.joinpath('_auto-md.jinja2')

        return None

    def as_templated_html(self):
        """
        Convert markdown to HTML (templated if a template is available)

        @return: HTML
        @rtype: str|unicode
        """

        template_path = self.find_template()
        if not template_path:
            return self.as_html()

        content = open(self.file_path, 'r').read()
        template = self.env.get(template_path)
        return template.as_html(content=content)


class LessFileHandler(BaseFileHandler):
    """
    Compile a provided less file
    """
    def match(self, file_path):
        """
        Match .less files

        @param file_path: File path
        @type file_path: path
        @return: Is a less file?
        @rtype: bool
        """
        return file_path.ext == '.less'

    def load(self, file_path):
        """
        Return a .less file representation

        @param file_path: Path to less file
        @type file_path: path
        @return: Less file
        @rtype: LessFile
        """
        f = LessFile(self.env, self)
        f.read_from(file_path)
        return f


class LessFile(BaseFile):
    """
    Represent a .less file
    """
    def read_from(self, file_path):
        """
        Read a given .less file

        @param file_path: Path to .less file
        @type file_path: path
        """
        self.file_path = file_path

    def write_to(self, file_path):
        """
        Write out a compiled version of the .less file to a given path

        @param file_path: Path for .css file
        @type file_path: path
        """
        self.ensure_output_dir(file_path)
        output_file_path = file_path.stripext() + '.css'

        os.system("lessc %s %s" % (self.file_path, output_file_path))


class BaseTypeHandler(object):
    """
    Base class for matching and loading Directory Types
    """
    def __init__(self, env):
        """
        Init the handler with the current environment

        @param env: Environment
        @type env: BuildEnvironment
        """
        self.env = env

    def match(self, dir_path, meta):
        """
        Match the path against the types this handler can process
        """
        raise NotImplementedError()

    def load(self, dir_path, meta):
        """
        Load a type for the given path
        """
        raise NotImplementedError()


class BaseType(object):
    """
    Base class for a Directory Type
    """
    def __init__(self, env, handler, dir_path, meta):
        """
        Init the type with the current env and handler

        @param env: Build environment
        @type env: BuildEnvironment
        @param handler: Handler for this type
        @type handler: BaseTypeHandler
        @param dir_path: Path to directory for type
        @type dir_path: path
        @param meta: Meta (usually from _index.yml)
        @type meta: dict

        """
        self.env = env
        self.handler = handler
        self.dir_path = dir_path
        self.meta = meta

    def process(self):
        """
        Perform whatever processing the type needs to do
        """
        raise NotImplementedError()

    def dispatch_dirs(self):
        """
        Helper to dispatch dirs
        """

        for d in self.dir_path.dirs():
            if d.startswith('_'):
                log.debug("Ignoring directory %s" % d)
                continue

            full_path = self.dir_path.joinpath(d)

            self.env.dispatch_type(full_path)


class DefaultTypeHandler(BaseTypeHandler):
    def match(self, dir_path, meta):
        """
        Default type, just parses the directory with handlers
        """
        if not meta.has_key('type') or meta['type'] == 'default':
            return True
        return False

    def load(self, dir_path, meta):
        """
        Load a default dir
        """
        return DefaultType(self.env, self, dir_path, meta)


class DefaultType(BaseType):
    def process(self):
        """
        Process a directory
        """

        for fn in self.dir_path.files():
            if fn.name.startswith('_'):
                # Ignore files starting with _
                log.debug("Ignoring file %s" % fn)
                continue

            log.debug("Getting file %s" % fn)
            f = self.env.get(fn)
            log.debug("Writing conversion of file %s" % fn)
            f.write_to(self.env.to_dest(fn))

        self.dispatch_dirs()


class BlogTypeHandler(BaseTypeHandler):
    def match(self, dir_path, meta):
        """
        Match against blog dirs
        """
        if meta.get('type', None) == 'blog':
            return True

    def load(self, dir_path, meta):
        """
        Load a blog dir
        """
        return BlogType(self.env, self, dir_path, meta)


class InvalidBlogPostError(Exception):
    pass


class BlogPost(object):
    """
    Blog post
    """
    env = None
    posted = None
    title = None
    filename = None
    file_path = None
    html = None

    def load_from(self, env, full_path):
        """
        Load in blog post
        """

        self.env = env

        m = re.search(r'^(\d+)-(\d+)-(\d+)-(\d+)-(\d+)-([^\.]+)', full_path.name)
        if not m:
            return False

        (year, month, day, hour, minute, title) = m.groups()
        self.posted = datetime(int(year), int(month), int(day), int(hour), int(minute))
        self.title = title
        self.filename = full_path.name
        self.file_path = full_path
        self.uri = self.env.map(full_path)

        return True

    def parse_content(self):
        """
        Open and parse the content of this blog post
        """
        handler = self.env.get(self.file_path)
        self.html = handler.as_html()


class BlogType(BaseType):
    posts = None

    def __init__(self, env, handler, dir_path, meta):
        """
        Init blog type
        """
        super(BlogType, self).__init__(env, handler, dir_path, meta)
        self.posts = []

    def process(self):
        """
        Process the blog directory, generating an index of posts suitable for use by the renderers
        """

        # Find posts
        for fn in self.dir_path.files():
            # Matches blog pattern?
            log.debug("Hunting for blog post in %s" % fn.name)
            post = BlogPost()
            if not post.load_from(self.env, self.dir_path.joinpath(fn)):
                # Not a blog post
                continue
            log.debug("Found blog post %s @ %s" % (post.title, post.posted))
            self.posts.append(post)

        # Sort posts
        self.posts.sort(lambda a, b: cmp(a.posted, b.posted))

        # Obtain content for each post in first pass
        for post in self.posts:
            post.parse_content()

        # Render all the posts
        # Get the renderer
        post_renderer = self.env.get(self.dir_path.joinpath(self.meta['post_renderer']))
        for post in self.posts:
            log.debug("Writing post %s to %s" % (post.title, self.env.map(post.file_path)))
            post_renderer.write_to(self.env.to_dest(post.file_path), post=post)

        # Render the index
        index_renderer = self.env.get(self.dir_path.joinpath(self.meta['index_renderer']))
        index_renderer.write_to(self.env.to_dest(self.dir_path.joinpath(self.meta['index_renderer'])), posts=self.posts)

        # Dispatch sub-dirs
        self.dispatch_dirs()


class PathMapBase(object):
    """
    Base class for Path Remappers
    """
    env = None

    def __init__(self, env):
        """
        Record environment for mapper
        @param env: Environment to map within
        @type env: BuildEnvironment
        """
        self.env = env

    def relative(self, file_path):
        """
        Map a source file path to a relative URL (relative path to destination)

        @param file_path: Source path
        @type file_path: path
        @return: Destination path
        @rtype: path
        """
        raise NotImplementedError()


class DefaultPathMap(PathMapBase):
    """
    Path mapper that just maps straight across.
    """

    def match(self, file_path):
        """
        Check to see whether this mapper matches this path
        @param file_path: Source path
        @type file_path: path
        @return: Does it match?
        @rtype: bool
        """
        return True

    def relative(self, file_path):
        """
        Map a source file path straight to destination path

        @param file_path: Source path
        @type file_path: path
        @return: Destination path
        @rtype: path
        """
        return file_path


class Jinja2PathMap(PathMapBase):
    """
    Path mapper that makes .jinja2 -> .html
    """
    def match(self, file_path):
        return file_path.ext == '.jinja2'

    def relative(self, file_path):
        return file_path.stripext() + '.html'


class MarkdownPathMap(PathMapBase):
    """
    Path mapper that makes .md -> .html
    """
    def match(self, file_path):
        return file_path.ext == '.md'

    def relative(self, file_path):
        return file_path.stripext() + '.html'


def watch_and_build(source_dir, destination_dir):
    """
    This is the autobuilder, which requires the watchdog package to work. Because we don't really want to
    *require* watchdog in case people are on funny platforms, we test for existence and only define then.

    @param source_dir: Source directory
    @type source_dir: str|unicode
    @param destination_dir: Destination directory
    @type destination_dir: str|unicode

    """

    try:
        import watchdog
        import watchdog.observers
        import watchdog.events
    except ImportError:
        watchdog = None
    if not watchdog:
        log.error("Cannot auto-build, you need the watchdog package installed. Try pip install watchdog")
        sys.exit(1)

    class FileChangeEventHandler(watchdog.events.FileSystemEventHandler):
        """
        File change event handler for triggering a build on file change
        """
        source_dir = None
        destination_dir = None

        def __init__(self, source_dir, destination_dir):
            """
            Set up event handler

            @param source_dir: Source directory
            @type source_dir: str|unicode
            @param destination_dir: Destination directory
            @type destination_dir: str|unicode
            """
            self.source_dir = source_dir
            self.destination_dir = destination_dir

        def on_any_event(self, event):
            """
            Call build

            @param event: Event
            @type event: watchdog.events.FileSystemEvent
            """
            log.warn("Change detected. Rebuilding")
            perform_build(self.source_dir, self.destination_dir)

    print "Monitoring source directory and rebuilding on change. ^C to stop"

    # Do one run immediately
    perform_build(source_dir, destination_dir)

    observer = watchdog.observers.Observer()
    observer.schedule(FileChangeEventHandler(source_dir, destination_dir), path=source_dir, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def perform_build(source_dir, destination_dir):
    """
    Perform a single build

    @param source_dir: Source directory
    @type source_dir: str|unicode
    @param destination_dir: Destination directory
    @type destination_dir: str|unicode
    """
    print "Building from %s to %s" % (source_dir, destination_dir)
    builder = Builder(source_dir, destination_dir)
    builder.register(Jinja2FileHandler)
    builder.register(MarkdownFileHandler)
    builder.register(LessFileHandler)
    builder.register(AnyFileHandler)

    builder.register_map(Jinja2PathMap)
    builder.register_map(MarkdownPathMap)
    builder.register_map(DefaultPathMap)

    builder.register_type(DefaultTypeHandler)
    builder.register_type(BlogTypeHandler)

    builder.clean()
    builder.build()
    print "Done"


if __name__ == "__main__":
    usage = "usage: %prog [options]"
    parser = OptionParser(usage="usage: %prog [options]")
    parser.add_option("--verbose","-v",
                      help = "print debugging output",
                      action = "store_true")
    parser.add_option("--monitor","-m",
                      help = "Monitor and rebuild whenever changes are detected",
                      action = "store_true")
    parser.add_option("--source","-s", type="string", default="source",
                      help = "Source directory")
    parser.add_option("--destination","-d", type="string", default="output",
                      help = "Destination directory")
    (options, args) = parser.parse_args()
    if options.verbose:
        log.setLevel(logging.DEBUG)

    log.debug("Verbose mode: %s" % options.verbose)

    source_dir = options.source
    destination_dir = options.destination

    if options.monitor:
        watch_and_build(source_dir, destination_dir)
    else:
        perform_build(source_dir, destination_dir)


