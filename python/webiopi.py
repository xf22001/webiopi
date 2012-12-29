#!/usr/bin/python

#   Copyright 2012 Eric Ptak - trouch.com
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.


import os
import sys
import time
import threading
import errno
import socket
import mimetypes as mime
import re
import base64
import _webiopi.GPIO as GPIO
import codecs
import hashlib
import fcntl
import termios
try:
    import BaseHTTPServer
except ImportError:
    import http.server as BaseHTTPServer

VERSION = '0.5.x'
SERVER_VERSION = 'WebIOPi/Python/' + VERSION

FUNCTIONS = {
    "I2C0": {"enabled": False, "gpio": {0:"SDA", 1:"SCL"}},
    "I2C1": {"enabled": True, "gpio": {2:"SDA", 3:"SCL"}},
    "SPI0": {"enabled": False, "gpio": {7:"CE1", 8:"CE0", 9:"MISO", 10:"MOSI", 11:"SCLK"}},
    "UART0": {"enabled": True, "gpio": {14:"TX", 15:"RX"}}
}

MAPPING = [[], [], []]
MAPPING[1] = ["V33", "V50", 0, "V50", 1, "GND", 4, 14, "GND", 15, 17, 18, 21, "GND", 22, 23, "V33", 24, 10, "GND", 9, 25, 11, 8, "GND", 7]
MAPPING[2] = ["V33", "V50", 2, "V50", 3, "GND", 4, 14, "GND", 15, 17, 18, 27, "GND", 22, 23, "V33", 24, 10, "GND", 9, 25, 11, 8, "GND", 7]
    
def runLoop(func=None):
    try:
        while True:
            if func != None:
                func()
            else:
                time.sleep(1)
            
    except KeyboardInterrupt:
        pass

def encodeAuth(login, password):
    abcd = "%s:%s" % (login, password)
    try:
        b = base64.b64encode(abcd)
    except TypeError:
        b = base64.b64encode(abcd.encode())
    return hashlib.sha256(b).hexdigest()

def log(message):
    print("%s %s" % (SERVER_VERSION, message))

def log_socket_error(message):
    log("Socket Error: %s" % message)

class Server(BaseHTTPServer.HTTPServer, threading.Thread):
    
    def __init__(self, port, login="webiopi", password="raspberry", context="webiopi", index="index.html", passwdfile=None):
        try:
            BaseHTTPServer.HTTPServer.__init__(self, ("", port), Handler)
        except socket.error as msg:
#            if (e_no == errno.EADDRINUSE):
#                raise Exception("Port %d already in use, try another one" % port)
#            else:
             raise Exception(msg)
            
        threading.Thread.__init__(self)
        self.port = port
        self.context = context
        self.docroot = "/usr/share/webiopi/htdocs"
        self.index = index
        self.callbacks = {}
        self.log_enabled = False
        self.auth = None
        
        if passwdfile != None and os.path.exists(passwdfile):
            print("Using stored login/password in %s" % passwdfile)
            f = open(passwdfile)
            self.auth = f.read().strip(" \r\n")
            f.close()
            
        elif login != None and password != None:
            print("Using login/password")
            self.auth = encodeAuth(login, password)
            
        if not self.context.startswith("/"):
            self.context = "/" + self.context
        if not self.context.endswith("/"):
            self.context += "/"
        self.start()
        
    def addMacro(self, callback):
        self.callbacks[callback.__name__] = callback

    def writeJSON(self, out):
        json = "{"
        first = True
        for (alt, value) in FUNCTIONS.items():
            if not first:
                json += ", "
            json += '"%s": %d' % (alt, value["enabled"])
            first = False
        
        json += ', "GPIO":{\n'
        first = True
        for gpio in range(GPIO.GPIO_COUNT):
            if not first:
                json += ", \n"

            function = GPIO.getFunctionString(gpio)
            value = GPIO.input(gpio)
                    
            json += '"%d": {"function": "%s", "value": %d' % (gpio, function, value)
            if GPIO.getFunction(gpio) == GPIO.PWM:
                (type, value) = GPIO.getPulse(gpio).split(':')
                json  += ', "%s": %s' %  (type, value)
            json += '}'
            first = False
            
        json += "\n}}"
        out.write(json.encode())

    def run(self):
        host = "[RaspberryIP]"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 53))
            (host, p) = s.getsockname()
            s.close()
        except (socket.error, e):
            pass

        self.running = True
        log("Started at http://%s:%s%s" % (host, self.port, self.context))
        try:
            self.serve_forever()
        except socket.error as msg:
            if self.running:
                log_socket_error(msg)
        log("Stopped")

    def stop(self):
        self.running = False
        self.server_close()
    
        
class Handler(BaseHTTPServer.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if self.server.log_enabled:
            log(format % args)

    def version_string(self):
        return SERVER_VERSION + ' ' + self.sys_version
    
    def checkAuthentication(self):
        if self.server.auth == None or len(self.server.auth) == 0:
            return True
        
        authHeader = self.headers.get('Authorization')
        if authHeader == None:
            return False
        
        if not authHeader.startswith("Basic "):
            return False
        
        auth = authHeader.replace("Basic ", "")
        try:
            hash = hashlib.sha256(auth).hexdigest()
        except TypeError:
            hash = hashlib.sha256(auth.encode()).hexdigest()
            
        if hash != self.server.auth:
            return False
        return True
        
    def do_GET(self):
        if not self.checkAuthentication():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="webiopi"')
            self.end_headers();
            return
        
        relativePath = self.path.replace(self.server.context, "/")
        if (relativePath.startswith("/")):
            relativePath = relativePath[1:];

        # JSON full state
        if relativePath == "*":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.server.writeJSON(self.wfile)
            
        # RPi header map
        elif relativePath == "map":
            json = "%s" % MAPPING[GPIO.BOARD_REVISION]
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.replace("'", '"').encode())

        # server version
        elif relativePath == "version":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(SERVER_VERSION.encode())

        # board revision
        elif relativePath == "revision":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            revision = "%s" % GPIO.BOARD_REVISION
            self.wfile.write(revision.encode())

        # Single GPIO getter
        elif (relativePath.startswith("GPIO/")):
            (mode, s_gpio, operation) = relativePath.split("/")
            gpio = int(s_gpio)

            value = None
            if (operation == "value"):
                if GPIO.input(gpio):
                    value = "1"
                else:
                    value = "0"
    
            elif (operation == "function"):
                value = GPIO.getFunctionString(gpio)
    
            elif (operation == "pwm"):
                if GPIO.isPWMEnabled(gpio):
                    value = "enabled"
                else:
                    value = "disabled"
                
            elif (operation == "pulse"):
                value = GPIO.getPulse(gpio)
                
            else:
                self.send_error(404, operation + " Not Found")
                return
                
            self.send_response(200)
            self.send_header("Content-type", "text/plain");
            self.end_headers()
            self.wfile.write(value.encode())

        # handle files
        else:
            if relativePath == "":
                relativePath = self.server.index
                            
            realPath = relativePath;
            
            if not os.path.exists(realPath):
                realPath = self.server.docroot + os.sep + relativePath
                
            if not os.path.exists(realPath):
                self.send_error(404, "Not Found")
                return

            realPath = os.path.realpath(realPath)
            
            if realPath.endswith(".py"):
                self.send_error(403, "Not Authorized")
                return
            
            if not (realPath.startswith(self.server.docroot) or realPath.startswith(os.getcwd())):
                self.send_error(403, "Not Authorized")
                return
                
            if (os.path.isdir(realPath)):
                realPath += os.sep + self.server.index;
                if not os.path.exists(realPath):
                    self.send_error(403, "Not Authorized")
                    return
                
            (type, encoding) = mime.guess_type(realPath)
            f = codecs.open(realPath, encoding="utf-8")
            data = f.read()
            f.close()
            self.send_response(200)
            self.send_header("Content-type", type);
#            self.send_header("Content-length", os.path.getsize(realPath))
            self.end_headers()
            try:
                self.wfile.write(data.encode(encoding="utf-8"))
            except UnicodeDecodeError:
                self.wfile.write(data)
            

    def do_POST(self):
        if not self.checkAuthentication():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="webiopi"')
            self.end_headers();
            return

        relativePath = self.path.replace(self.server.context, "")
        if (relativePath.startswith("/")):
            relativePath = relativePath[1:];

        if (relativePath.startswith("GPIO/")):
            (mode, s_gpio, operation, value) = relativePath.split("/")
            gpio = int(s_gpio)
            
            try:
                if (operation == "value"):
                    if (value == "0"):
                        GPIO.output(gpio, GPIO.LOW)
                    elif (value == "1"):
                        GPIO.output(gpio, GPIO.HIGH)
                    else:
                        self.send_error(400, "Bad Value")
                        return
        
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(value.encode())
    
                elif (operation == "function"):
                    value = value.lower()
                    if value == "in":
                        GPIO.setFunction(gpio, GPIO.IN)
                    elif value == "out":
                        GPIO.setFunction(gpio, GPIO.OUT)
                    elif value == "pwm":
                        GPIO.setFunction(gpio, GPIO.PWM)
                    else:
                        self.send_error(400, "Bad Function")
                        return
                    value = GPIO.getFunctionString(gpio)
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(value.encode())
    
                elif (operation == "sequence"):
                    (period, sequence) = value.split(",")
                    period = int(period)
                    GPIO.outputSequence(gpio, period, sequence)
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(sequence[-1].encode())
                    
                elif (operation == "pwm"):
                    if value == "enable":
                        GPIO.enablePWM(gpio)
                    elif value == "disable":
                        GPIO.disablePWM(gpio)
                    
                    if GPIO.isPWMEnabled(gpio):
                        result = "enabled"
                    else:
                        result = "disabled"
                    
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(result.encode())
                    
                elif (operation == "pulse"):
                    GPIO.pulse(gpio)
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write("OK".encode())
                    
                elif (operation == "pulseRatio"):
                    ratio = float(value)
                    GPIO.pulseRatio(gpio, ratio)
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(value.encode())
                    
                elif (operation == "pulseAngle"):
                    angle = float(value)
                    GPIO.pulseAngle(gpio, angle)
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain");
                    self.end_headers()
                    self.wfile.write(value.encode())
                    
                else: # operation unknown
                    self.send_error(404, operation + " Not Found")
                    return
            except (GPIO.InvalidDirectionException, GPIO.InvalidChannelException) as e:
                self.send_error(403, "%s" % e)
                return
                
        elif (relativePath.startswith("macros/")):
            (mode, fname, value) = relativePath.split("/")
            if (fname in self.server.callbacks):
                callback = self.server.callbacks[fname]

                if ',' in value:
                    args = value.split(',')
                    result = callback(*args)
                elif len(value) > 0:
                    result = callback(value)
                else:
                    result = callback()
                     
                self.send_response(200)
                self.send_header("Content-type", "text/plain");
                self.end_headers()
                if result:
                    result = "%s" % result
                    self.wfile.write(result.encode())
            else:
                self.send_error(404, fname + " Not Found")
                return
                
        else: # path unknowns
            self.send_error(404, "Not Found")

class Serial:
    def __init__(self, baudrate=9600, port="/dev/ttyAMA0"):
        aname = "B%d" % baudrate
        if not hasattr(termios, aname):
            raise Exception("Unsupported baudrate")
        speed = getattr(termios, aname)

        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NDELAY)
        
        if self.fd < 0:
            raise Exception("Cannot open %s" % port)
        
        fcntl.fcntl(self.fd, fcntl.F_SETFL, os.O_NDELAY)
        
        backup  = termios.tcgetattr(self.fd)
        options = termios.tcgetattr(self.fd)
        
        options[4] = speed # ispeed
        options[5] = speed # ospeed
        
        # cflags
        options[2] |= (termios.CLOCAL | termios.CREAD)
        options[2] &= ~termios.PARENB
        options[2] &= ~termios.CSTOPB
        options[2] &= ~termios.CSIZE
        options[2] |= termios.CS8
        
        termios.tcsetattr(self.fd, termios.TCSADRAIN, options)

    def close(self):
        os.close(self.fd)
        
    def write(self, string):
        os.write(self.fd, string)

    def read(self, bytecount=1):
        return os.read(self.fd, bytecount)

def main(argv):
    port = 8000
    passwdfile = "/etc/webiopi/passwd"

    if len(argv)  == 2:
        port = int(argv[1])
    
    server = Server(port=port, passwdfile=passwdfile)
    runLoop()
    server.stop()

if __name__ == "__main__":
    main(sys.argv)
