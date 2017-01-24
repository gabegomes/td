#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Johannes Steinmetzer
# PYTHON_ARGCOMPLETE_OK

import argparse
import csv
import itertools
import logging
import os
import re
import sys

import numpy as np
from tabulate import tabulate
# Optional modules
try:
    import argcomplete
except ImportError:
    pass
try:
    from docx import Document
except ImportError:
    pass

from helper_funcs import chunks

# Regex
# Excitation energies and oscillator strengths
# Groups: id, spin symm, spat symm, dE, wavelength, osc. strength, S**2
EXC_LINE = "Excited State\s+(\d+):\s+([\w\.]+)-([\?\w'\"]+)\s+([0-9\.-]+) eV" \
           "\s+([0-9\.-]+) nm\s+f=([0-9\.-]+)\s+<S\*\*2>=([0-9\.]+)"
# ExcitedStates between MOs and corresponding CI-coefficients
# Groups: initial MO, final MO, ci coeffcient
TRS_LINE = r"([\dAB]+)\s*(->|<-)\s*([\dAB]+)\s*\s+([0-9\.-]+)"

CONV_DICT = {
        "s": str,
        "i": int,
        "f": float,
}


class ExcitedState:
    def __init__(self, id, spin, spat, dE, l, f, s2):
        self.id = id
        self.spin = spin
        self.spat = spat
        self.dE = dE
        self.l = l
        self.f = f
        self.s2 = s2
        self.rr_weight = None

        self.mo_transitions = list()

    def as_list(self, attrs=None):
        if not attrs:
            attrs = ("id",
                     "spin",
                     "spat",
                     "dE",
                     "l",
                     "f",
                     "s2")
        return [getattr(self, a) for a in attrs]


    def add_mo_transition(self, start_mo, to_or_from, final_mo, ci_coeff,
                          start_spin, final_spin,
                          contrib=None,
                          start_irrep="A", final_irrep="A"):
        if start_spin == "":
            start_spin = "alpha"
        if final_spin == "":
            final_spin = "alpha"
        self.mo_transitions.append(MOTransition(
            start_mo, to_or_from, final_mo, ci_coeff,
            contrib, start_spin, final_spin, start_irrep, final_irrep)
        )

    def get_start_mos(self):
        return set([mo_tr.start_mo for mo_tr in self.mo_transitions])

    def get_final_mos(self):
        return set([mo_tr.final_mo for mo_tr in self.mo_transitions])

    def has_mo_transition(self, start_mo, final_mo):
        return bool(
            [mo_exc for mo_exc in self.mo_transitions
             if (start_mo == mo_exc.start_mo) and
                (final_mo == mo_exc.final_mo)]
        )

    def is_singlet(self):
        return self.spin == "Singlet".lower()

    def calculate_contributions(self):
        for mo_trans in self.mo_transitions:
            if mo_trans.contrib is not None:
                continue
            contrib = mo_trans.ci_coeff**2
            if self.is_singlet():
                contrib *= 2
            mo_trans.contrib = contrib

    def correct_backexcitations(self):
        # Check if there are any back-excitations, e.g.
        # 89B <- 90B
        back_transitions = [bt for bt in self.mo_transitions
                            if bt.to_or_from == "<-"]
        for bt in back_transitions:
            final_mo = bt.start_mo
            start_mo = bt.final_mo
            # Find corresponding transition from final_mo -> start_mo
            trans_to_correct = [t for t in self.mo_transitions
                                if (t.start_mo == final_mo and
                                    t.final_mo == start_mo)][0]
            # Correct contribution of trans_to_correct
            trans_to_correct.contrib -= bt.contrib

    def print_mo_transitions(self, verbose_mos):
        for mo_trans in self.mo_transitions:
            # Suppresss backexcitations like
            # 89B <- 90B
            if mo_trans.to_or_from == "<-":
                continue
            print(mo_trans.outstr())
            if verbose_mos:
                print("\t\t{0} -> {1}").format(
                    verbose_mos[int(mo_trans.start_mo)],
                    verbose_mos[int(mo_trans.final_mo)]
                )

    def suppress_low_ci_coeffs(self, thresh):
        # Check if excitation lies below ci coefficient threshold
        # if it does don't add this excitation
        contrib_thresh = thresh**2 * 2
        to_del = list()
        for mo_trans in self.mo_transitions:
            if mo_trans.contrib:
                if mo_trans.contrib <= contrib_thresh:
                    to_del.append(mo_trans)
                continue
            if abs(mo_trans.ci_coeff) <= thresh:
                to_del.append(mo_trans)
        for td in to_del:
            self.mo_transitions.remove(td)

    def calc_rr_weight(self, rr_exc):
        l_in_cm = 10**7 / self.l
        rr_ex_in_cm = 10**7 / rr_exc
        G = 1500j
        self.rr_weight = self.f * abs(G/(l_in_cm-rr_ex_in_cm-G))

    def __str__(self):
        print("#{0} {1} eV f={2}").format(self.id, self.dE, self.f)


class MOTransition:
    def __init__(self, start_mo, to_or_from, final_mo, ci_coeff,
                 contrib, start_spin, final_spin,
                 start_irrep, final_irrep):
        self.start_mo = start_mo
        self.start_spin = start_spin
        self.to_or_from = to_or_from
        self.final_mo = final_mo
        self.final_spin = final_spin
        self.ci_coeff = ci_coeff
        self.contrib = contrib
        self.start_irrep = start_irrep
        self.final_irrep = final_irrep

    def outstr(self):
        return "\t{0:>5s}{1} {2} {3} {4:>5s}{5} {6}" \
               "\t{7: 5.3f}\t{8:3.1%}".format(
                self.start_mo,
                self.start_irrep,
                self.start_spin[0],
                self.to_or_from,
                self.final_mo,
                self.final_irrep,
                self.final_spin[0],
                self.ci_coeff,
                self.contrib)

    def __str__(self):
        return self.outstr()


def conv(to_convert, fmt_str):
    return [CONV_DICT[t](item) for item, t in zip(to_convert, fmt_str)]


def print_table(excited_states):
    as_list = [exc_state.as_list() for exc_state in excited_states]
    floatfmt = ["", "", "", ".2f", ".1f", ".5f", ""]
    print(tabulate(as_list,
                   headers=["#", "2S+1", "Spat.", "dE in eV", "l in nm",
                            "f", "<S**2>"],
                   floatfmt=floatfmt))


"""
def print_mos(id, mos, mo_names, is_singlet):
    if mo_names:
        start_name = final_name = "NO NAME"
        if start_mo in mo_names:
            start_name = mo_names[start_mo]
        if final_mo in mo_names:
            final_name = mo_names[final_mo]
        print("{}\t->\t{}".format(
            start_name, final_name,))
"""


def get_excited_states(file_name):
    handle = open(file_name, "r")
    excited_states = list()
    involved_mos = dict()

    matched_exc_state = False
    for line in handle:
        line = line.strip()
        # look for initial and final MO and corresponding CI-coefficient
        if matched_exc_state:
            match_obj = re.match(TRS_LINE, line)
            if match_obj:
                group_list = list(match_obj.groups())
                conv_mo_excitation = conv(group_list, "sssf")
                last_exc_state = excited_states[-1]
                last_exc_state.add_mo_transition(*conv_mo_excitation)
                try:
                    involved_mos[last_exc_state.id].append(
                            conv_mo_excitation)
                except KeyError:
                    involved_mos[last_exc_state.id] = [conv_mo_excitation, ]
            # stop this matching if a blank line is encountered
            if line.strip() == "":
                matched_exc_state = False
            continue
        m_obj = re.match(EXC_LINE, line)
        if m_obj:
            excited_state = ExcitedState(*conv(m_obj.groups(), "issffff"))
            excited_states.append(excited_state)
            matched_exc_state = True

    handle.close()

    return excited_states, involved_mos


def parse_escf(fn):
    with open("escf.out") as handle:
        text = handle.read()

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
    dom_contribs = list()
    for sym, ee, osc, dc in zip(syms, ees, oscs, dcs_parsed):
        id_, spin, spat = sym
        dE = ee * 27.211386
        l = 45.56335 / ee

        exc_state = ExcitedState(id_, spin, spat, dE, l, osc, "???")
        excited_states.append(exc_state)
        for d in dc:
            start_mo = d[0]
            start_irrep = d[1]
            final_mo = d[4]
            final_irrep = d[5]
            to_or_from = "->"
            contrib = float(d[8]) / 100
            exc_state.add_mo_transition(start_mo, to_or_from, final_mo,
                                        ci_coeff=-0, contrib=contrib,
                                        start_spin=d[2], final_spin=d[6],
                                        start_irrep=start_irrep,
                                        final_irrep=final_irrep)

    return excited_states, dom_contribs


def gaussian_logs_completer(prefix, **kwargs):
    print(prefix)
    print(kwargs)
    return [path for path in os.listdir(".") if path.endswith(".out") or
            path.endswith(".log")]


def gauss_uv_band(l, f, l_i):
    return (1.3062974e8 * f / (1e7 / 3099.6) *
            np.exp(-((1. / l - 1. / l_i) / (1. / 3099.6))**2))


def print_impulse(f, l):
    #print(l, 0)
    print(l, f)
    #print(l, 0)


def make_spectrum(excited_states, start_l, end_l, normalized,
                  highlight_impulses=None, e2f=False):
    # According to:
    # http://www.gaussian.com/g_whitepap/tn_uvvisplot.htm
    # wave lengths and oscillator strengths
    fli = [(es.f, es.l) for es in excited_states]
    x = np.arange(start_l, end_l, 0.5)
    spectrum = list()
    for l in x:
        spectrum.append(np.sum([gauss_uv_band(l, f, l_i) for f, l_i in fli]))
    spectrum = np.array(spectrum)
    if e2f:
        spectrum /= 40490.05867167
    if normalized:
        spectrum = spectrum / spectrum.max()
    # Output spectrum
    for x, y in zip(x, spectrum):
        print(x, y)

    # Output oscillator strength impulses
    print()
    print()
    for f, l in fli:
        print_impulse(f, l)

    if highlight_impulses:
        print
        print
        for f, l in [fli[i - 1] for i in highlight_impulses]:
            print_impulse(f, l)

    """
    Used for printing also the gauss bands of the
    n-highest transitions
    # Sort by f
    fli_sorted = sorted(fli, key=lambda tpl: -tpl[0])
    highest_fs = fli_sorted[:15]
    print
    x = np.arange(200, 600, 0.5)
    highest_bands = list()
    for f, l_i in highest_fs:
        band = lambda l: gauss_uv_band(l, f, l_i)
        calced_band = band(x)
        calced_band = band(x) / calced_band.max() * f * 3
        highest_bands.append(calced_band)
        #for xi, ai in zip(x, calced_band):
        #    print xi, ai
        #print
    highest_bands_headers = tuple(['"l_i = {} nm, f = {}"'.format(
        l_i, f) for f, l_i in highest_fs])
    headers = ('"l in nm"', "Sum") + highest_bands_headers
    wargel = zip(x, spectrum, *highest_bands)
    print tabulate(wargel, headers=headers, tablefmt="plain")
    """


def make_docx(excited_states):
    # Check if docx was imported properly
    if "docx" not in sys.modules:
        logging.error("Could't import python-docx-module.")
        sys.exit()
    header = ("State",
              "λ / nm",
              "E / eV",
              "f",
              "Transition",
              "Weight / \%")
    attrs = ("id", "l", "dE", "f")

    # Prepare the document and the table
    doc = Document()
    # We need one additional row for the table header
    table = doc.add_table(rows=len(excited_states)+1,
                          cols=len(header))

    # Prepare the data to be inserted into the table
    as_lists = [es.as_list(attrs) for es in excited_states]
    as_fmt_lists = [(
        "S{}".format(id),
        "{:.1f}".format(l),
        "{:.2f}".format(dE),
        "{:.4f}".format(f)) for id, l, dE, f in as_lists]
    # Set header in the first row
    for item, cell in zip(header, table.rows[0].cells):
        cell.text = item
    for i, fmt_list in enumerate(as_fmt_lists, 1):
        for item, cell in zip(fmt_list, table.rows[i].cells):
            cell.text = item
    doc.save("Haha.docx")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
            "Displays output from Gaussian-td-calculation,"
            " sorted by oscillator strength f."
    )
    parser.add_argument("--show", metavar="n", type=int,
                        help="Show only the first n matching excitations.")
    parser.add_argument("--only-first", metavar="only_first", type=int,
                        help="Only consider first n excited states.")
    parser.add_argument("--range", metavar="start_end", nargs="+", type=float,
                        help="Show only excited states accessible in this "
                             "wavelength range (e.g. 400 450).")
    parser.add_argument("--sf", action="store_true",
                        help="Sort by oscillator strength.")
    parser.add_argument("--start-mos", dest="start_mos", type=str, nargs="+",
                        help="Show only transitions from this MO.")
    parser.add_argument("--final-mos", dest="final_mos", type=str, nargs="+",
                        help="Show only transitions to this MO.")
    parser.add_argument("--start-final-mos", dest="start_final_mos",
                        type=str, nargs="+", help="(Number of) MO pair(s). "
                        "Only transitions from [start mo] to [final mo] "
                        "are shown.")
    parser.add_argument("--raw", action="store_true",
                        help="Just print the data, without the table.")
    parser.add_argument("--by-id", dest="by_id", type=int,
                        help="Display excited state with specific id.")
    parser.add_argument("--csv",
                        help="Read csv file containing verbose MO-names.")
    parser.add_argument("--summary", action="store_true",
                        help="Print summary to stdout.")
    parser.add_argument("--ci-coeff", dest="ci_coeff", type=float,
                        default=0.2, help="Only consider ci coefficients "
                        "not less than.")
    parser.add_argument("--spectrum", dest="spectrum", type=float, nargs=2,
                        help="Calculate the UV spectrum from the TD "
                        "calculation (FWHM = 0.4 eV).")
    parser.add_argument("--e2f", dest="e2f", action="store_true",
                        help="Used with spectrum. Converts the molecular "
                        "extinctions coefficients on the ordinate to "
                        "oscillator strengths.")
    parser.add_argument("--hi", dest="highlight_impulses", type=int,
                        nargs="+", help="List of excitations. Their "
                        "oscillator strength bars will be printed separatly.")
    parser.add_argument("--nnorm", dest="normalized", action="store_false",
                        help="Don't normalize the calculated spectrum.",
                        default=True)
    parser.add_argument("--irrep", dest="irrep",
                        help="Filter for specific irrep.")
    parser.add_argument("--booktabs", dest="booktabs", action="store_true",
                        help="Output table formatted for use with the latex-"
                        "package booktabs.")
    parser.add_argument("--exc", type=float,
                        help="Excitation wavelength for resonance raman.")
    parser.add_argument("--rrthresh", type=float, default=1e-2,
                        help="Threshold for RR weight.")
    parser.add_argument("--fthresh", type=float, default=0.0,
                        help="Only show transitions with oscillator strengths"
                        " greater than or equal to the supplied threshold.""")
    parser.add_argument("--chunks", type=int, default=0,
                        help="Split the output in chunks. Useful for "
                        "investigating excited state optimizations. Don't use "
                        "with --sf or --raw.")
    parser.add_argument("--docx", action="store_true",
                        help="Output the parsed data as a table into a "
                        ".docx document.")
    # Use the argcomplete module for autocompletion if it's available
    if "argcomplete" in sys.modules:
        parser.add_argument("file_name", metavar="fn",
                            help="File to parse.").completer = gaussian_logs_completer
        argcomplete.autocomplete(parser)
    else:
        parser.add_argument("file_name", metavar="fn",
                            help="File to parse.")
    args = parser.parse_args()

    # Try to look for csv file with MO names
    # [root of args.file_name] + "_mos.csv"
    root_file_name = os.path.splitext(args.file_name)[0]
    look_for_csv_name = root_file_name + "_mos.csv"
    if (not args.csv) and os.path.exists(look_for_csv_name):
        args.csv = look_for_csv_name
    # Load MO names from csv
    verbose_mos = dict()
    if args.csv:
        with open(args.csv, "r") as csv_handle:
            csv_reader = csv.reader(csv_handle, delimiter="\t")
            for row in csv_reader:
                id, mo_type = row
                verbose_mos[int(id)] = mo_type

    # Extract excitations
    if args.file_name == "escf.out":
        excited_states, mos = parse_escf(args.file_name)
    else:
        excited_states, mos = get_excited_states(args.file_name)
    for exc_state in excited_states:
        exc_state.calculate_contributions()
        exc_state.correct_backexcitations()
        exc_state.suppress_low_ci_coeffs(args.ci_coeff)

    if args.only_first:
        excited_states = excited_states[:args.only_first]

    if args.spectrum:
        # Starting and ending wavelength of the spectrum to be calculated
        start_l, end_l = args.spectrum
        make_spectrum(excited_states, start_l, end_l, args.normalized,
                      args.highlight_impulses, args.e2f)
        sys.exit()

    if args.by_id:
        try:
            exc_state = excited_states[args.by_id-1]
            print_table([exc_state, ])
            exc_state.print_mo_transitions(verbose_mos)
        except IndexError:
            print("Excited state with id #{} not found.".format(args.by_id))
        sys.exit()

    """
    !
    ! DO FILTERING/SORTING HERE
    !
    """

    if args.irrep:
        excited_states = [es for es in excited_states if es.spat == args.irrep]

    if args.start_mos:
        states = set()
        for start_mo in args.start_mos:
            states.update([exc_state for exc_state in excited_states
                           if start_mo in exc_state.get_start_mos()])
        excited_states = states
    if args.final_mos:
        states = set()
        for final_mo in args.final_mos:
            states.update([exc_state for exc_state in excited_states
                           if final_mo in exc_state.get_final_mos()])
        excited_states = states
    if args.start_final_mos:
        sf_mos = args.start_final_mos
        if (len(sf_mos) % 2) != 0:
            sys.exit("Need an even number of arguments for "
                     "--start-final-mos, not an odd number.")
        states = set()
        pairs = [(sf_mos[i], sf_mos[i+1]) for i in range(len(sf_mos) / 2)]
        for start_mo, final_mo in pairs:
            states.update(
                [exc_state for exc_state in excited_states if
                 exc_state.has_mo_transition(start_mo, final_mo)]
            )
        excited_states = states

    # Sort by oscillator strength if requested.
    # Sorting by energy is the default.
    if args.sf:
        excited_states = sorted(excited_states,
                                key=lambda exc_state: -exc_state.f)
    else:
        excited_states = sorted(excited_states,
                                key=lambda exc_state: exc_state.dE)
        # Relabel the states from 1 to len(excited_states)
        for i, es in enumerate(excited_states, 1):
            es.id = i
    # Only show excitations in specified wavelength-range
    if args.range:
        # Only lower threshold specified (energy wise)
        if len(args.range) is 1:
            end = args.range[0]
            excited_states = [exc_state for exc_state in excited_states
                              if (exc_state.l >= end)]
        elif len(args.range) is 2:
            start, end = args.range
            excited_states = [exc_state for exc_state in excited_states
                              if (start <= exc_state.l <= end)]
        else:
            raise Exception("Only 1 or 2 arguments allowed for --range!")

    if args.fthresh:
        excited_states = [exc_state for exc_state in excited_states
                          if (exc_state.f >= args.fthresh)]

    excited_states = list(excited_states)[:args.show]

    """
    !
    ! DON'T DO FILTERING/SORTING BELOW THIS LINE
    !
    """

    # find lowest orbital from where an excitation originates
    min_mo = min(itertools.chain(*[exc_state.get_start_mos()
                                   for exc_state in excited_states]))
    # find highest lying orbital where an excitation ends
    max_mo = max(itertools.chain(*[exc_state.get_final_mos()
                                   for exc_state in excited_states]))

    # Convert remaining excitations to a list so it can be printed by
    # the tabulate module
    as_list = [exc_state.as_list() for exc_state in excited_states]

    if args.exc:
        for es in excited_states:
            es.calc_rr_weight(args.exc)
    """
    !
    ! PRINTING BELOW THIS LINE
    !
    """

    if args.booktabs:
        nr, mult, sym, eV, nm, f, spin = zip(*as_list)
        eV = ["{:.2f}".format(_) for _ in eV]
        f = ["{:.4f}".format(_) for _ in f]
        nm = ["{:.1f}".format(_) for _ in nm]
        for_booktabs = zip(nr, sym, eV, nm, f)
        print(tabulate(for_booktabs, tablefmt="latex_booktabs"))
        sys.exit()

    # Dont print the pretty table when raw output is requested
    # Don't print anything after the summary
    if args.raw:
        for exc_state in as_list:
            print("\t".join([str(item) for item in exc_state]))
        # Don't print lowest starting MO etc. ...
        sys.exit()
    if args.docx:
        make_docx(excited_states)
        sys.exit()
    elif args.summary:
        for exc_state in excited_states:
            print_table([exc_state, ])
            exc_state.print_mo_transitions(verbose_mos)
            print("")
    else:
        # Print as pretty table with header information
        if args.chunks > 0:
            for i, chunk in enumerate(chunks(excited_states, args.chunks), 1):
                print("### Chunk {} ###".format(i))
                print_table(chunk)
                print()
        else:
            print_table(excited_states)

    if args.exc:
        rr_weights = [(es.id, es.rr_weight) for es in excited_states
                      if es.rr_weight >= args.rrthresh]
        print(tabulate(rr_weights))

    print("Only considering transitions  with "
          "CI-coefficients >= {}:".format(args.ci_coeff))
    print("Lowest excitation from MO {}.".format(min_mo))
    print("Highest excitation to MO {}.".format(max_mo))
