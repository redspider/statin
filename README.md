## Statin, the site renderer

Statin has the following goals:

 * Trivial installation and use
 * Excellent base to start from
 * Decent looking
 * Support for rendering complex templates, straight HTML and simple Markdown files
 * Easy to read and understand

Statin solves these problems using the following strategies:

### Trivial installation and use

To create a new statin site:

```
git clone https://github.com/redspider/statin.git my-new-site
cd my-new-site
virtualenv .
source bin/activate
pip install -r requirements.txt
pip install watchdog # recommended for auto-build, not required
```

Then you're done. you can run build.py from there and it'll build this site, which you can immediately
start editing.

If you need to deploy the result, well that's easy too. You can write a two-line shell script:

```
python build.py
rsync output/ ssh://my-awesome-host.com/my-site/
```

Or that could be a copy into your Dropbox folder or whatever.

To avoid having to run build.py every time you make a change, use the --monitor switch and it'll watch
and autobuild.

### Excellent base to start from

By virtue of cloning, you get this site, which is based on Bootstrap and all ready to
roll. In addition, this site is set up so you've got a set of example articles and you can see how that all
works. Just get in there, modify a couple of files and it's all you.

### Decent looking

It's bootstrap, but it's not just bootstrap, the example pages all aim to look fairly good out of the box
without being complex so that you can easily stamp your own style on 'em.

### Support for rendering complex templates, straight HTML and simple Markdown files

Sometimes you need to do complicated stuff, maybe you wanna render an index of your blog posts or automatically
build a menu or gallery based on the contents of a directory. For that stuff you can write .jinja2 templates.

These templates can do inheritance and all kinds of other shiny, they have extra support that allows them to
list other files in the static source tree and inspect the output of those files. As an example:

```
{% for article in glob('articles/*.md') %}
    {{ select(grab(article),'h1').text() }}
{% endfor %}
```

This gets the names of all the Markdown files in the articles/ dir and iterates through them. For
each one it grabs the rendered output and then does a jQuery-style selector on the result to get the H1 tag. It
then takes the text from that tag.

Sometimes on the other hand, you want to keep things dead simple. When you're writing a blog post you don't want
to have to fiddle with paragraph tags or anything. For those you can write markdown files, just end 'em in ```.md```.

The plain output of a Markdown file would be boring, so instead you get to choose how they're rendered by
creating a special template called ```_auto-md.jinja2```. This template is just like any other jinja2 template
except it receives the content of the Markdown file as the variable ```content```. You can wrap this in markdown
tags to have it processed, or you could do other fancy stuff.

Finally, you can put anything else in the tree and it'll just be copied across - HTML, images, whatever. The only
other thing that gets messed around with is ```.less``` which gets compiled if you have ```lessc``` available in your path.

### Easy to read and understand

Code is nice and clear and mostly comments. It's easy to add your own URL mapping or process new file
types if you want.
