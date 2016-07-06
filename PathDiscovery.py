#!/usr/bin/python
"""
Created on Oct 8, 2014

@author: Dmitrii
"""

import time
import threading
import Messages

import routing_logging

lock = threading.Lock()

PATH_DISCOVERY_LOG = routing_logging.create_routing_log("routing.path_discovery.log", "path_discovery")
PATH_DISCOVERY_LOG.info("INITIALIZING PATH_DISCOVERY...")
PATH_DISCOVERY_LOG.info("INITIALIZING PATH_DISCOVERY2...")
PATH_DISCOVERY_LOG.info("INITIALIZING PATH_DISCOVERY3...")


class PathDiscoveryHandler(threading.Thread):
    def __init__(self, app_queue, wait_queue, rrep_queue, arq_handler, table):
        super(PathDiscoveryHandler, self).__init__()
        self.wait_queue = wait_queue
        self.rreq_list = {}
        self.rreq_thread_list = {}
        self.running = True

        self.arq_handler = arq_handler
        self.table = table

        # Starting a thread for handling incoming RREP requests
        self.rrep_handler_thread = RrepHandler(app_queue, rrep_queue, self.rreq_list, self.rreq_thread_list)
        self.rrep_handler_thread.start()

    def run(self):
        while self.running:
            src_ip, dst_ip, raw_data = self.wait_queue.get()
            # Check if the dst_ip in the current list of requests
            if dst_ip in self.rreq_list:

                lock.acquire()
                self.rreq_list[dst_ip].append([src_ip, dst_ip, raw_data])

                PATH_DISCOVERY_LOG.info("Got DST_IP in rreq list: %s", dst_ip)
                PATH_DISCOVERY_LOG.debug("RREQ LIST: %s", self.rreq_list[dst_ip])

                lock.release()

            # If the request is new, start a new request thread, append new request to rreq_list
            else:

                PATH_DISCOVERY_LOG.info("No DST_IP in rreq list: %s", dst_ip)

                lock.acquire()
                self.rreq_list[dst_ip] = [[src_ip, dst_ip, raw_data]]
                self.rreq_thread_list[dst_ip] = RreqRoutine(self.arq_handler, self.table,
                                                            self.rreq_list, self.rreq_thread_list, src_ip, dst_ip)
                lock.release()

                self.rreq_thread_list[dst_ip].start()

    def quit(self):
        self.running = False
        # Stopping RREP handler
        self.rrep_handler_thread.quit()
        self.rrep_handler_thread._Thread__stop()
        # Stopping all running rreq_routines
        for i in self.rreq_thread_list:
            self.rreq_thread_list[i].quit()


# A routine thread for periodically broadcasting RREQs
class RreqRoutine(threading.Thread):
    def __init__(self, arq_handler, table, rreq_list, rreq_thread_list, src_ip, dst_ip):
        super(RreqRoutine, self).__init__()
        self.running = True
        # self.raw_transport = raw_transport

        self.arq_handler = arq_handler
        self.table = table

        self.rreq_list = rreq_list
        self.rreq_thread_list = rreq_thread_list
        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.node_mac = table.node_mac

        self.dsr_header = Messages.DsrHeader(2)       # Type 2 corresponds to RREQ service message
        self.max_retries = 3
        self.interval = 5

    def run(self):
        count = 0
        while self.running:
            if count < self.max_retries:
                self.send_rreq()
                time.sleep(self.interval)
            else:
                # Max retries reached. Delete corresponding packets from rreq_list, stop the thread

                PATH_DISCOVERY_LOG.info("Maximum retries reached!!! Deleting the thread...")

                lock.acquire()
                if self.rreq_list.get(self.dst_ip):
                    del self.rreq_list[self.dst_ip]
                if self.rreq_thread_list.get(self.dst_ip):
                    del self.rreq_thread_list[self.dst_ip]
                lock.release()

                # Stop the thread
                self.quit()
                
            count += 1
            
    # Generate and send RREQ
    def send_rreq(self):
        rreq = Messages.RouteRequest()
        rreq.src_ip = self.src_ip
        rreq.dst_ip = self.dst_ip
        rreq.dsn = 1
        rreq.hop_count = 1
        
        # Prepare a dsr_header
        self.dsr_header.src_mac = self.node_mac
        self.dsr_header.tx_mac = self.node_mac

        self.arq_handler.arq_send(rreq, self.dsr_header, self.table.get_neighbors())

        PATH_DISCOVERY_LOG.info("New  RREQ for IP: '%s' has been sent. Waiting for RREP", str(self.dst_ip))
        
    def quit(self):
        self.running = False
        self._Thread__stop()


# Class for handling incoming RREP messages
class RrepHandler(threading.Thread):
    def __init__(self, app_queue, rrep_queue, rreq_list, rreq_thread_list):
        super(RrepHandler, self).__init__()
        self.app_queue = app_queue
        self.rrep_queue = rrep_queue
        self.rreq_list = rreq_list
        self.rreq_thread_list = rreq_thread_list
        self.running = True
        
    def run(self):
        while self.running:
            src_ip = self.rrep_queue.get()

            PATH_DISCOVERY_LOG.info("Got RREP. Deleting RREQ thread...")

            if src_ip in self.rreq_list:
                lock.acquire()
                # Get the packets from the rreq_list
                data = self.rreq_list[src_ip]
                thread = self.rreq_thread_list[src_ip]
                # Delete the entry from rreq_list and stop corresponding rreq_thread
                del self.rreq_list[src_ip]
                thread.quit()
                del self.rreq_thread_list[src_ip]
                lock.release()

                # Send the packets back to original app_queue
                for packet in data:

                    PATH_DISCOVERY_LOG.info("Putting delayed packets in app_queue...")
                    PATH_DISCOVERY_LOG.debug("Packet dst_ip: %s", str(packet[1]))

                    self.app_queue.put(packet)
                
    def quit(self):
        self.running = False
