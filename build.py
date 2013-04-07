#!env python
"""
Current issues:

 * You can probably go into an infinite loop pretty easily using grab
 * Image resizing/conversion
 * Perhaps create a couple of intermediate classes for things like HTMLOutputFile or something
 * Metadata from .yml files
"""
import sys
import time

import os, re, jinja2, markdown2
import jinja2.ext
from path import path
from pyquery import PyQuery as pq
from optparse import OptionParser
import logging

logging.basicConfig(level=logging.WARN)
log = logging.getLogger('statix')


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
        for root, dirs, files in os.walk(str(self.env.source_dir)): # Sadly using os.walk because path.walk is broken
            # Remove any directories starting with _, we ignore those for build
            for d in dirs:
                if d.startswith('_'):
                    log.debug("Ignoring directory %s" % d)
                    dirs.remove(d)

            # Render each file
            for fn in [self.env.source_dir.joinpath(root, file_path) for file_path in files]:

                if fn.name.startswith('_'):
                    # Ignore files starting with _
                    log.debug("Ignoring file %s" % fn)
                    continue

                log.debug("Getting file %s" % fn)
                f = self.env.get(fn)
                log.debug("Writing conversion of file %s" % fn)
                f.write_to(self.env.to_dest(fn))


class BuildEnvironment(object):
    """
    Contains the environment and various helpers for Files
    """
    source_dir = None
    dest_dir = None
    handlers = None
    mappers = None
    jinja2_env = None

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

    def write_to(self, file_path):
        """
        Write out a Jinja2 file to the given path

        @param file_path: Destination file path
        @type file_path: path
        """

        self.ensure_output_dir(file_path)
        open(file_path, 'w').write(self.template.render())

    def as_html(self, **kwargs):
        """
        Return jinja2 file as HTML (result of render)

        @return: HTML
        @rtype: basestring
        """
        return self.template.render(**kwargs)


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
        log.error("Cannot autobuild, you need the watchdog package installed. Try pip install watchdog")
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

    log.warn("Monitoring source directory and rebuilding on change. ^C to stop")

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

    builder = Builder(source_dir, destination_dir)
    builder.register(Jinja2FileHandler)
    builder.register(MarkdownFileHandler)
    builder.register(LessFileHandler)
    builder.register(AnyFileHandler)

    builder.register_map(Jinja2PathMap)
    builder.register_map(MarkdownPathMap)
    builder.register_map(DefaultPathMap)

    builder.clean()
    builder.build()


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


