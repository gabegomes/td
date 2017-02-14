#!/usr/bin/env python2
# -*- coding: utf-8 -*-

import re

from td.helper_funcs import EV2NM
from td.ExcitedState import ExcitedState


def parse_ricc2(text):
    num_sym_spin_re = "symmetry, multiplicity:\s*(\d+)\s*([\w\"\']+)\s*(\d+)"
    ids_syms_spins = re.findall(num_sym_spin_re, text)
    ids, syms, spins = zip(*ids_syms_spins)

    exc_energy_re = "frequency\s*:.+?([\d\.]+)\s*e\.V."
    ees = [float(ee) for ee in re.findall(exc_energy_re, text)]

    osc_strength_re = "\(mixed gauge\)\s*:\s*([\d\.]+)"
    oscs = [float(osc) for osc in re.findall(osc_strength_re, text)]

    mo_contrib_re = "occ\. orb\..+?\%\s*\|(.+?)\s*norm"
    # Get the blocks containing the MO contributions for every state
    mo_contrib_blocks = re.findall(mo_contrib_re, text, re.DOTALL)
    mo_contribs = list()
    for moc in mo_contrib_blocks:
        # Split at newline and drop first and last line
        moc_lines = moc.strip().split("\n")[1:-1]
        split = [re.sub("[\|\(\)]", "",  mol).split() for mol in moc_lines]
        # Add orbital spin if we we have a closed shell wave function
        for line in split:
            if len(line) == 8:
                line.insert(3, "a")
                line.insert(7, "a")
        mo_contribs.append(split)

    assert(len(syms) == len(spins) == len(ees) == len(oscs) ==
           len(mo_contribs))

    excited_states = list()
    for id, spin, sym, ee, osc, moc in zip(ids, spins, syms, ees, oscs,
                                           mo_contribs):
        l = EV2NM / ee
        exc_state = ExcitedState(id, spin, sym, ee, l, osc, "???")
        excited_states.append(exc_state)
        for (start_mo, start_irrep, _, start_spin,
             final_mo, final_irrep, _, final_spin,
             coeff, percent) in moc:
            to_or_from = "->"
            exc_state.add_mo_transition(start_mo,
                                        to_or_from,
                                        final_mo,
                                        ci_coeff=coeff,
                                        contrib=float(percent)/100,
                                        start_spin=start_spin,
                                        final_spin=final_spin,
                                        start_irrep=start_irrep,
                                        final_irrep=final_irrep)

    return excited_states, mo_contribs

if __name__ == "__main__":
    fn = "/home/carpx/Code/td/logs/ricc2.out"
    with open(fn) as handle:
        text = handle.read()
    parse_ricc2(text)
