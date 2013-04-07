#!env python
"""
Current issues:

 * Tree based handlers don't work if you use obtain, because obtain is at a different point in picking them up
 * You can probably go into an infinite loop pretty easily using grab
 * Code is shit
 * Not sure how to do URL remapping for blog posts you want url'd by date or something
 * Other meta-data like don't-post-it-yet - fuckit, put it in a different dir
 * Post-processing on markdown or other content?
 * Selectors on markdown source instead of output?
"""

import glob
import os, shutil, re, jinja2, markdown2, lxml
from path import path
from jinja2 import BaseLoader, TemplateNotFound
from os.path import join, exists, getmtime
from pyquery import PyQuery as pq



from optparse import OptionParser
import logging
logging.basicConfig(level=logging.WARN)
log = logging.getLogger('statix')

import jinja2.ext

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
        @type lines: basestring
        @return Normalised lines
        @rtype basestring

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

    def register(self, handler):
        """
        Add a file handler
        @param handler: File handler
        @type handler: class
        """

        log.debug("Registering handler %r" % handler)
        self.handlers.append(handler(self))

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

    def to_dest(self, file_path):
        """
        Convert a source path to a destination path

        @param file_path: Source path
        @type file_path: path
        @return Destination path
        @rtype str
        """
        # Obtain the relative path from source to file, then add that to the destination
        return self.dest_dir.joinpath(self.source_dir.relpathto(file_path))


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
        @type path
        """
        self.file_path = file_path
    
    def write_to(self, file_path):
        """
        Write the file to the given file_path by copying from the read path. This will create any directories required
        to succeed.

        @param file_path: Path to write to
        @type file_path: path
        """

        # Ensure parent directory exists
        if not file_path.parent.isdir():
            file_path.parent.makedirs()
        
        self.file_path.copy(file_path)


class Jinja2FileHandler(BaseFileHandler):
    """
    File handler for .jinja2 files
    """

    jinja2env = None

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
        self.template = self.handler.jinja2env.get_template(file_path)

    def write_to(self, file_path):
        """
        Write out a Jinja2 file to the given path

        @param file_path: Destination file path
        @type file_path: path
        """
        open(file_path, 'w').write(self.template.render())

    def as_html(self):
        """
        Return jinja2 file as HTML (result of render)

        @return: HTML
        @rtype: basestring
        """
        return self.template.render()


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
        Write out result of Markdown processing to given path.

        @param file_path: Output file path
        @type file_path: path
        """

        open(file_path, 'w').write(self.as_html())

    def as_html(self):
        """
        Convert markdown to HTML (not templated, useful for parsing)

        @return: HTML
        @rtype: str|unicode
        """
        return self.handler.markdown.convert(open(self.file_path, 'r').read())


if __name__ == "__main__":
    usage = "usage: %prog [options]"
    parser = OptionParser(usage="usage: %prog [options]")
    parser.add_option("--verbose","-v",
                      help = "print debugging output",
                      action = "store_true")
    (options, args) = parser.parse_args()
    if options.verbose:
        log.setLevel(logging.DEBUG)

    log.debug("Verbose mode: %s" % options.verbose)

    builder = Builder('source', 'output')
    builder.register(AnyFileHandler)
    builder.clean()
    builder.build()
