import SimpleHTTPServer
import SocketServer

class CS144Handler(SimpleHTTPServer.SimpleHTTPRequestHandler):

    # Disable logging DNS lookups
    def address_string(self):
        return str(self.client_address[0])

    # Serve files
	def do_GET(self):
		if self.path=="/":
			self.path="/index.html"

		try:
			#Check the file extension required and
			#set the right mime type

			sendReply = False
			if self.path.endswith(".html"):
				mimetype='text/html'
				sendReply = True
			if self.path.endswith(".jpg"):
				mimetype='image/jpg'
				sendReply = True

			if sendReply == True:
				#Open the static file requested and send it
				f = open(curdir + sep + self.path)
				self.send_response(200)
				self.send_header('Content-type',mimetype)
				self.end_headers()
				self.wfile.write(f.read())
				f.close()
			return


		except IOError:
			self.send_error(404,'File Not Found: %s' % self.path)

PORT = 80

Handler = CS144Handler
httpd = SocketServer.TCPServer(("", PORT), Handler)
print "Server1: httpd serving at port", PORT
httpd.serve_forever()
