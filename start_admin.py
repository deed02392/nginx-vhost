#!/usr/bin/python
import os
import sys
import signal
import re
import struct
import pwd
import binascii
import tornado.web
import tornado.ioloop
import tornado.template
import sqlite3
import random
import subprocess
import lockfile
from tornado import escape
from pprint import pprint
from passlib.context import CryptContext

current_dir = os.path.dirname(os.path.realpath(__file__))
templates_dir = "templates"
create_site_file = "scripts/create_site.sh"
remove_site_file = "scripts/remove_site.sh"
db_file = "lib/codeclub.db3"
pwd_context = CryptContext(schemes=["sha256_crypt"],default="sha256_crypt")

def on_exit(sig, func=None):
    tornado.ioloop.IOLoop.instance().add_callback(shutdown)
    print "Exiting..."
    sys.exit(1)

def shutdown():
    application.stop()
    io_loop = tornado.ioloop.IOLoop.instance()
    deadline = time.time() + 3

    def stop_loop():
        now = time.time()
        if now < deadline and (io_loop._callbacks or io_loop._timeouts):
            io_loop.add_timeout(now + 1, stop_loop)
        else:
            io_loop.stop()
    stop_loop()
    del db
    
class Utils:
    valid_fullname = re.compile(r"^[A-Za-z]+(?: ?[A-Za-z]+)*$")
    valid_username = re.compile(r"^[a-z]+$")
    
    @staticmethod
    def string_cap(s, l):
        return s if len(s)<=l else s[0:l-3]+'...'
    
    @staticmethod
    def fullname_to_username(fullname):
        fullname = fullname.lower()
        fullname = ''.join(fullname.split()) # Kill whitespace
        fullname = Utils.string_cap(fullname, 30)
        if Utils.valid_username.match(fullname):
            return fullname
        else:
            return False
    
    @staticmethod
    def create_password():
        zoo_animals = ['elephant', 'lion', 'tiger', 'giraffe', 'penguin', 'gorillas', 'sharks',
            'panda', 'meerkat', 'crocodile', 'bear', 'otter', 'wolf', 'cheetah', 'snake', 'zebra',
            'frog', 'dolphin']
        fruits = ['strawberry', 'mango', 'watermelon', 'banana', 'orange', 'apple', 'grape',
            'peach', 'cherry', 'raspberry', 'kiwi', 'blueberry', 'lemon', 'pear', 'plum',
            'blackberry', 'lime']
        number = ord(struct.unpack("<c", os.urandom(1))[0])
        return random.choice(fruits) + random.choice(zoo_animals) + str(number)
    
    @staticmethod
    def create_site(fullname, username, password, url, indexed):
        create_site_path = os.path.join(current_dir, create_site_file)
        create_lock = lockfile.FileLock(create_site_path)
        with create_lock:
            proc = subprocess.Popen([create_site_path, fullname, username, password, url], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdoutdata, stderrdata = proc.communicate()
            if proc.returncode == 0:
                return 0
            else:
                return stdoutdata, stderrdata

    @staticmethod
    def remove_site(username):
        remove_site_path = os.path.join(current_dir, remove_site_file)
        remove_lock = lockfile.FileLock(remove_site_path)
        with remove_lock:
            proc = subprocess.Popen([remove_site_path, username], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdoutdata, stderrdata = proc.communicate()
            if proc.returncode == 0:
                return 0
            else:
                return stdoutdata, stderrdata
    @staticmethod
    def hash_password(password, salt=None):
        if not salt:
            salt = binascii.hexlify(os.urandom(32))
        
        return scrypt.encrypt(salt, password, hash_for_secs), salt

class Users:
    def add_user(self, fullname, username, password, url, indexed):
        db.query(("INSERT INTO students (fullname, username, password, url, indexed) "
                    "VALUES (?, ?, ?, ?, ?)"), [fullname, username, password, 'http://' + url + '.code.club', indexed])
    
    def get_students(self):
        self.__sync_db_users()
        return self.__get_db()
    
    def get_indexed_students(self):
        self.__sync_db_users()
        db_users = self.__get_db()
        return [student for student in db_users if student['isindexed']]
    
    def is_available(self, username):
        self.__sync_db_users()
        db_users = self.__get_db()
        system_users = self.__get_system()
        
        system_code_club_usernames = [u['username'] for u in system_users]
        db_usernames = [u['username'] for u in db_users]

        current_usernames = system_code_club_usernames + db_usernames

        if username in current_usernames:
            return False
        else:
            return True

    def remove_user(self, username):
        db.query("DELETE FROM students WHERE username=?", [username])
    
    def is_student(self, username):
        c = db.query("SELECT username FROM students WHERE username=?", [username])
        result = c.fetchone()
        if result:
            return True
        else:
            return False
        
    def __sync_db_users(self):
        db_users = self.__get_db()
        system_users = self.__get_system()
        
        system_code_club_usernames = [u['username'] for u in system_users if u['gecos'] == "Code Club student"]
        db_usernames = [u['username'] for u in db_users]
        orphaned_usernames = list(set(system_code_club_usernames) - set(db_usernames))
        if len(orphaned_usernames) > 0:
            for username in orphaned_usernames:
                db.query("INSERT INTO students (username) VALUES (?)", [username])
    
    def __get_db(self):
        c = db.query("SELECT fullname, username, password, url, indexed FROM students")
        students = []
        for record in c.fetchall():
            students.append({
                'fullname': record[0],
                'username': record[1],
                'password': record[2],
                'url': record[3],
                'isindexed': record[4],
                'deletelink': '/del/' + record[1],
            })
        return students
    
    def __get_system(self):
        pwd_users = pwd.getpwall()
        users = []
        for u in pwd_users:
            users.append({
                'username': u[0],
                'password': None,
                'gecos': u[4],
            })
        return users

class LoginHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("login.htm.template")
        return
    
    def post(self):
        password = self.get_argument('password')
        c = db.query("SELECT password FROM admin WHERE oid=1")
        hashed_password = c.fetchone()[0]
        
        if pwd_context.verify(password, hashed_password):
            self.set_secure_cookie("logged_in", "admin")
            self.redirect(self.get_argument('next'))
        else:
            self.render("login.htm.template", badguess=1)        
        

class AdminHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("logged_in")
    
    @tornado.web.authenticated
    def get(self):
        users = Users()
        students = users.get_students()
        
        self.render("admin.htm.template", students=students)

class PasswordHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("logged_in")

    @tornado.web.authenticated
    def post(self):
        password = self.get_argument('password')
        password = pwd_context.encrypt(password)
        db.query(("INSERT OR REPLACE INTO admin (oid, password) "
                    "VALUES (1, ?)"), [password])
        self.redirect("/admin.htm")
        
class AddHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("logged_in")

    @tornado.web.authenticated
    def post(self):
        users = Users()
        fullname = escape.xhtml_escape(self.get_argument('fullname'))
        username = escape.xhtml_escape(self.get_argument('username'))
        password = self.get_argument('password')
        url = escape.xhtml_escape(self.get_argument('url'))
        try:
            indexed = 1 if self.get_argument('indexed') == "on" else 0
        except tornado.web.MissingArgumentError:
            indexed = 0
        
        if not fullname:
            self.write("Full Name must be specified")
            return
        elif not Utils.valid_fullname.match(fullname):
            self.write("Full Name must just be letters with a single space separating the names")
            return

        if not username:
            username = Utils.fullname_to_username(fullname)
            if not username:
                self.write("Please specify a username, I couldn't create one from %s" % fullname)
                return
        if not password:
            password = Utils.create_password()
        if not url:
            url = username
        
        if not users.is_available(username):
            self.write("This username already exists (%s). Specify the username if two people in your class have the same full name." % username)
            return
        
        create_site = Utils.create_site(fullname, username, password, url, indexed)
        if create_site != 0:
            self.write("<pre>%s %s %s %s %r\n" % (fullname, username, password, url, indexed))
            self.write("stdout: %s\nstderr: %s" % (create_site[0], create_site[1]))
            return
        
        users.add_user(fullname, username, password, url, indexed)
        self.redirect("/admin.htm")

class DelHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("logged_in")

    @tornado.web.authenticated
    def get(self, username):
        users = Users()
        
        if not users.is_student(username):
            self.write("This username has already been deleted (%s)." % username)
            return

        delete_site = Utils.remove_site(username)
        if delete_site != 0:
            self.write("<pre>%s\n" % (username))
            self.write("stdout: %s\nstderr: %s" % (delete_site[0], delete_site[1]))
            return
        users.remove_user(username)
        self.redirect("/admin.htm")

class IndexHandler(tornado.web.RequestHandler):
    def get(self, path):
        if path:
            self.redirect("/")
            return
        users = Users()
        students = users.get_indexed_students()
        
        self.render("index.htm.template", students=students)
        
application = tornado.web.Application([
    (r"/admin.htm", AdminHandler),
    (r"/add", AddHandler),
    (r"/pass", PasswordHandler),
    (r"/del/([a-z]+)", DelHandler),
    (r"/login.htm", LoginHandler),
    (r"/(.*)", IndexHandler),
],
debug=True,
login_url="/login.htm",
cookie_secret=binascii.hexlify(os.urandom(32)),
template_path=os.path.join(current_dir, templates_dir))

class DatabaseHandler(object):
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.cur = self.conn.cursor()
    
    def query(self, arg, tuple=()):
        self.cur.execute(arg, tuple)
        self.conn.commit()
        return self.cur
    
    def __del__(self):
        print "Closing db"
        self.conn.close()

if __name__ == "__main__":
    application.listen(8080, "127.0.0.1")
    db = DatabaseHandler(os.path.join(current_dir, db_file))
    db.query(("CREATE TABLE IF NOT EXISTS students "
            "(fullname TEXT, username TEXT UNIQUE, password TEXT, url TEXT, indexed INT)"))
    db.query(("CREATE TABLE IF NOT EXISTS admin "
            "(password TEXT NOT NULL)"))
    
    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)
    
    tornado.ioloop.IOLoop.instance().start()