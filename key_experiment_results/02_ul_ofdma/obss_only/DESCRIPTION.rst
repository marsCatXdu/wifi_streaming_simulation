obss-contention-ul-ofdma-comparison
===================================

Purpose
-------

This matrix compares five target-sender approaches under identical
traffic, propagation, and random seeds. UL OFDMA states: disabled, enabled.

Target devices and approaches
-----------------------------

* ``Single 2.4 GHz interface``: one dual-radio 802.11be STA node is
  associated through separate non-MLO interfaces with the target AP's
  2.4 GHz and 5 GHz BSSs; the application sends only through the
  2.4 GHz interface.
* ``Single 5 GHz interface``: the same dual-radio association is used,
  but the application sends only through the 5 GHz interface.
* ``Application full duplication``: the same frame is sent over both
  independent non-MLO 802.11be interfaces.
* ``MLO NMaxInflights=1``: one two-link 802.11be STR MLD STA is
  associated with one two-link AP MLD; each MPDU may be in flight on
  at most one link.
* ``MLO NMaxInflights=2``: the same MLD association permits an MPDU to
  be in flight on both links opportunistically.

Both target links are 20 MHz: channel 1 at 2.4 GHz and channel 36 at
5 GHz. Target data uses EHT MCS 5, an 800 ns guard interval, BE
traffic, A-MPDU aggregation, and a 500-packet MAC queue.
The target STA is 10 m from the target AP and generates 30
frames/s for 60 s. Interframes are
12000 bytes; every
60th frame is multiplied by
4 and packetized into
1200-byte UDP payloads.

Overlapping BSS devices
-----------------------

Four independent APs each serve 4 same-standard STAs:

* one 802.11n BSS on the 2.4 GHz target channel;
* one 802.11ax BSS on the 2.4 GHz target channel;
* one 802.11ac BSS on the 5 GHz target channel;
* one 802.11be BSS on the 5 GHz target channel.

These 16 OBSS STAs do not associate with the target AP. Every OBSS
STA has independent uplink and downlink UDP ON/OFF flows. A new
UL rate is drawn uniformly from 0.5-
3 Mbps and a new DL rate from
2-8
Mbps for each ON period. Mean ON and OFF durations are 100 ms and
300 ms. OBSS PHY rates use Minstrel-HT with the latest amendment
only and a 50 ms update interval.

Device counts per approach
--------------------------

Each approach contains 1 logical target STA node and
16 contention STA nodes. The target AP is one logical
node with either two independent AP interfaces or one two-link AP
MLD. Four additional AP nodes exist for the OBSS profile.

UL OFDMA configuration
----------------------

When disabled, all uplink data uses normal EDCA channel access. When
enabled, ``RrMultiUserScheduler`` is installed on the target EHT AP.
The independent HE and EHT OBSS APs also use the scheduler.
HT and VHT APs remain EDCA-only. Only associated HE/EHT STAs are
trigger eligible; OFDMA does not coordinate stations across BSS
boundaries.

The scheduler requests access every 20
ms, enables BSRP, allocates RUs to at most
4 STAs, and uses
1200 bytes as the fallback solicited
PSDU size. Each treatment uses 10 RNG seeds and is paired by seed.
