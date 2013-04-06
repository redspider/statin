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
from optparse import OptionParser
import logging
logging.basicConfig(level=logging.WARN)
log = logging.getLogger('statix')

import jinja2.ext

class Markdown2Extension(jinja2.ext.Extension):
    tags = set(['markdown'])

    def __init__(self, environment):
        super(Markdown2Extension, self).__init__(environment)
        environment.extend(
            markdowner=markdown2.Markdown(extras=['fenced-code-blocks','footnotes','header-ids'])
        )

    def parse(self, parser):
        lineno = parser.stream.next().lineno
        body = parser.parse_statements(
            ['name:endmarkdown'],
            drop_needle=True
        )
        return jinja2.nodes.CallBlock(
            self.call_method('_markdown_support'),
            [],
            [],
            body
        ).set_lineno(lineno)

    def normalise_lines(self, s):
        """
        Take the first set of whitespace on the first line, and strip the remaining lines by that much whitespace.
        """
        size = 0
        detected = False
        output = []
        for l in s.split("\n"):
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


from jinja2 import BaseLoader, TemplateNotFound
from os.path import join, exists, getmtime
from pyquery import PyQuery as pq

def jinja2_process_template(filename):
    env = jinja2.Environment(extensions=[Markdown2Extension], loader=jinja2.FileSystemLoader('source'))
    env.globals['grab'] = jinja2_grab
    env.globals['select'] = jinja2_select
    env.globals['glob'] = jinja2_glob
    return env.get_template(filename)

def jinja2_glob(path):
    return [re.sub(r'^source/','',p) for p in glob.glob('source/' + path)]

def jinja2_grab(filename):
    return obtain(filename)

def jinja2_select(html, selector):
    return pq(html)(selector)

def to_source(filename):
    return os.path.join('source',filename)

def to_target(filename):
    if filename.endswith('.jinja2'):
        filename = re.sub(r'\.jinja2$','.html',filename)
    return os.path.join('output',filename)

def to_dir(d):
    path = os.path.split(d)
    if path[0] == '':
        path = path[1:]
    if len(path) == 1:
        return ''
    return os.path.join(*(path[1:]))

def obtain(filename):
    file_extension = os.path.splitext(filename)[1]

    if filename.endswith('.jinja2'):
        log.debug("Rendering jinja2 template %s to %s" % (to_source(filename), to_target(filename)))
        t = jinja2_process_template(filename)
        return t.render()
    elif file_extension in handlers:
        log.debug("Found handler for file extension %s" % file_extension)
        content = open(to_source(filename), 'r').read()
        t = jinja2_process_template(handlers[file_extension])
        return t.render(content=jinja2.Markup(content))
    else:
        return open(to_source(filename)).read()

handlers = dict()

def process(directory, filename):
    """
    Convert files as necessary

    Dead simple strategy. If it starts with _ we ignore it. If it's .jinja2, we load and convert it. Otherwise, we just copy it.

    """
    global handlers
    full_path = os.path.join(directory, filename)

    if filename.startswith('_auto-'):
        m = re.search(r'^_auto-([^\.]+)\.', filename)
        if m:
            log.debug("Found a handler for %s" % m.group(1))
            handlers["." + m.group(1)] = full_path
        else:
            log.warn("Found a handler file called %s which didn't match expected filename format" % filename)

    if filename.startswith('_'):
        log.debug("%s begins with an underscore, ignoring" % filename)
        return


    file_extension = os.path.splitext(filename)[1]
    if filename.endswith('.jinja2'):
        log.debug("Rendering jinja2 template %s to %s" % (to_source(filename), to_target(filename)))
        t = jinja2_process_template(full_path)
        open(to_target(full_path),'w').write(t.render({}))
    elif file_extension == '.less':
        log.debug("Found less file, compiling to css")
        os.system('lessc %s %s' % (to_source(full_path), re.sub(r'.less$','.css',to_target(full_path))))
    elif file_extension in handlers:
        log.debug("Found handler for file extension %s" % file_extension)
        content = open(to_source(full_path), 'r').read()
        t = jinja2_process_template(handlers[file_extension])
        open(to_target(full_path),'w').write(t.render(content=jinja2.Markup(content)))
    else:
        log.debug("Copying %s to %s" % (to_source(full_path), to_target(full_path)) )
        shutil.copy(to_source(full_path), to_target(full_path))

def build():
    """
    Build the static tree
    """
    log.debug("Wiping out target")
    shutil.rmtree('output')
    os.mkdir('output')
    log.debug("Initiating build")
    for root, dirs, files in os.walk('source'):
        for d in dirs:
            if d.startswith('_'):
                dirs.remove(d)
        for d in dirs:
            log.debug("Making dir %s" % d)
            os.makedirs(os.path.join('output', to_dir(root), d))
        for filename in files:
            process(to_dir(root), filename)


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

    build()