#!/bin/bash
# exp2 test -- run inside a terminal host (h1 or h2).
#
#   usage: bash exp2_test.sh [h1|h2]
#
# Prints each command and its output as:
#   > <command>
#
#   <output>
#
# h1: ingress ports of r1, r2, r3 -> then ping peer h2 -> traceroute h2
# h2: ingress ports of r3, r2, r1 -> then ping peer h1 -> traceroute h1

ROLE="${1:-h1}"

if [ "$ROLE" = "h2" ]; then
    HOPS=(10.0.3.1 10.0.23.2 10.0.12.1)
    PEER=10.0.1.11
else
    HOPS=(10.0.1.1 10.0.12.2 10.0.23.3)
    PEER=10.0.3.22
fi

run() {
    echo "> $*"
    echo
    "$@" 2>&1
    echo
}

# requirement 2: ping every router's ingress port
for hop in "${HOPS[@]}"; do
    run ping -c 4 -n "$hop"
done

# ping the peer terminal host
run ping -c 4 -n "$PEER"

# requirement 3: traceroute to the peer terminal host
run traceroute -n "$PEER"
