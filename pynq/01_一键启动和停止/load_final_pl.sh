#!/bin/bash
set -e
sudo systemctl restart pynq-load-hp1-bitstream.service
systemctl status pynq-load-hp1-bitstream.service --no-pager | sed -n '1,80p'
