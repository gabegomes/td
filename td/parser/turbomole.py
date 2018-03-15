#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re

from td.constants import EV2NM, HARTREE2EV, HARTREE2NM
from td.ExcitedState import ExcitedState


def parse_ricc2(text):
    num_sym_spin_re = "symmetry, multiplicity:\s*(\d+)\s*([\w\"\']+)\s*(\d+)"
    ids_syms_spins = re.findall(num_sym_spin_re, text)
    ids, syms, spins = zip(*ids_syms_spins)

    exc_energy_re = "frequency\s*:.+?([\d\.]+)\s*e\.V."
    ees = [float(ee) for ee in re.findall(exc_energy_re, text)]

    #osc_strength_re = "\(mixed gauge\)\s*:\s*([\d\.]+)"
    osc_strength_re = "oscillator strength.+?length gauge\)\s*:\s*([\d\.]+)"
    oscs = [float(osc) for osc in re.findall(osc_strength_re, text)]

    mo_contribs = list()
    mo_contrib_re = "occ\. orb\..+?\%\s*\|(.+?)\s*norm"
    # Get the blocks containing the MO contributions for every state
    mo_contrib_blocks = re.findall(mo_contrib_re, text, re.DOTALL)
    for mcb in mo_contrib_blocks:
        block_lines = mcb.strip().split("\n")[1:-1]
        split_lines = [re.sub("[\|\(\)]", "",  mol).split()
                       for mol in block_lines]
        for sl in split_lines:
            if len(sl) == 8:
                sl.insert(3, "a")
                sl.insert(7, "a")
        mo_contribs.append(split_lines)

    # When excited state properties are requested the lists
    # 'syms', 'spins', 'ees' will be twice as long as 'oscs'
    # and 'mo_contribs', but they got the same data in both 
    # halves. So we drop the second half.
    assert(len(syms) == len(spins) == len(ees))
    if len(syms) == (2 * len(oscs)):
        first_half = slice(len(oscs))
        syms = syms[first_half]
        spins = spins[first_half]
        ees = ees[first_half]

    #import pdb; pdb.set_trace()
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

    return excited_states


def parse_escf(text):
    # In openshell calculations TURBOMOLE omits the multiplicity in
    # the string.
    sym = "(\d+)\s+(singlet|doublet|triplet|quartet|quintet|sextet)?" \
          "\s*([\w'\"]+)\s+excitation"
    sym_re = re.compile(sym)
    syms = sym_re.findall(text)
    syms = [(int(id_), spin, spat) for id_, spin, spat in syms]

    exc_energy = "Excitation energy:\s*([\d\.E\+-]+)"
    exc_energy_re = re.compile(exc_energy)
    ees = exc_energy_re.findall(text)
    ees = [float(ee) for ee in ees]

    osc_strength = "mixed representation:\s*([\d\.E\+-]+)"
    osc_strength_re = re.compile(osc_strength)
    oscs = osc_strength_re.findall(text)
    oscs = [float(osc) for osc in oscs]

    dom_contrib = "2\*100(.*?)Change of electron number"
    dom_contrib_re = re.compile(dom_contrib, flags=re.MULTILINE | re.DOTALL)
    dcs = dom_contrib_re.findall(text)
    dc_str = "(\d+) ([\w'\"]+)\s*(beta|alpha)?\s+([-\d\.]+)\s*" \
             "(\d+) ([\w'\"]+)\s*(beta|alpha)?\s+([-\d\.]+)\s*" \
             "([\d\.]+)"
    dc_re = re.compile(dc_str)
    dcs_parsed = [dc_re.findall(exc) for exc in dcs]

    excited_states = list()
    for sym, ee, osc, dc in zip(syms, ees, oscs, dcs_parsed):
        id_, spin, spat = sym
        dE = ee * HARTREE2EV
        l = HARTREE2NM / ee

        exc_state = ExcitedState(id_, spin, spat, dE, l, osc, "???")
        excited_states.append(exc_state)
        for d in dc:
            start_mo = d[0]
            start_irrep = d[1]
            start_spin = d[2]
            final_mo = d[4]
            final_irrep = d[5]
            final_spin = d[6]
            to_or_from = "->"
            contrib = float(d[8]) / 100
            exc_state.add_mo_transition(start_mo, to_or_from, final_mo,
                                        ci_coeff=-0, contrib=contrib,
                                        start_spin=start_spin,
                                        final_spin=final_spin,
                                        start_irrep=start_irrep,
                                        final_irrep=final_irrep)

    return excited_states
