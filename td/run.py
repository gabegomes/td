#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Johannes Steinmetzer, 2017
# PYTHON_ARGCOMPLETE_OK

import argparse
import itertools
import logging
import os
import re
import shutil
import sys

from jinja2 import Environment, FileSystemLoader
import numpy as np
import simplejson as json

from td.ExcitedState import ExcitedState
from td.tabulate import tabulate
import td.parser.gaussian as gaussian
import td.parser.orca as orca
import td.parser.turbomole as turbo
from Spectrum import Spectrum

# Optional modules
try:
    import argcomplete
except ImportError:
    pass
try:
    from docx import Document
except ImportError:
    pass

THIS_DIR = os.path.dirname(os.path.realpath(__file__))


def print_table(excited_states):
    as_list = [exc_state.as_list() for exc_state in excited_states]
    floatfmt = ["", "", "", ".2f", ".1f", ".5f", ""]
    print(tabulate(as_list,
                   headers=["#", "2S+1", "Spat.", "dE in eV", "l in nm",
                            "f", "<S**2>"],
                   floatfmt=floatfmt))


def gaussian_logs_completer(prefix, **kwargs):
    return [path for path in os.listdir(".") if path.endswith(".out") or
            path.endswith(".log")]


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]


def as_table(excited_states, verbose_mos, newline_str="\n"):
    # The table header
    header = ("State",
              "λ / nm",
              "E / eV",
              "f",
              "Transition",
              "Weight")
    attrs = ("id", "l", "dE", "f")
    trans_fmt = "{} ({} {}) → {} ({} {})"
    weight_fmt = "{:.0%}"

    # Prepare the data to be inserted into the table
    as_lists = [es.as_list(attrs) for es in excited_states]
    as_fmt_lists = [[
        "S{}".format(id),
        "{:.1f}".format(l),
        "{:.2f}".format(dE),
        "{:.4f}".format(f)] for id, l, dE, f in as_lists]

    # For one excited state there may be several transitions
    # that contribute. This loop constructs two string, holding
    # the information about the transitions and the contributing
    # weights.
    for i, exc_state in enumerate(excited_states):
        mo_trans = exc_state.mo_transitions
        trans_str_list = list()
        # This loops over all MOTransitions for ONE excited state
        for mot in mo_trans:
            start_tpl = mot.start_tpl()
            final_tpl = mot.final_tpl()
            try:
                verbose_start_mo = verbose_mos[start_tpl]
                verbose_final_mo = verbose_mos[final_tpl]
            except:
                verbose_start_mo = ""
                verbose_final_mo = ""

            trans_str = trans_fmt.format(
                            verbose_start_mo,
                            mot.start_mo,
                            mot.start_irrep,
                            verbose_final_mo,
                            mot.final_mo,
                            mot.final_irrep)
            trans_str_list.append(trans_str)
        weight_list = [weight_fmt.format(mot.contrib)
                       for mot in mo_trans]
        weight_str = newline_str.join(weight_list)
        trans_str = newline_str.join(trans_str_list)
        as_fmt_lists[i].extend([trans_str, weight_str])

    return as_fmt_lists, header


def make_docx(excited_states, verbose_mos):
    """Export the supplied excited states into a .docx-document."""
    # Check if docx was imported properly. If not exit.
    if "docx" not in sys.modules:
        logging.error("Could't import python-docx-module.")
        sys.exit()

    docx_fn = "export.docx"
    as_fmt_lists, header = as_table(excited_states, verbose_mos)

    # Prepare the document and the table
    doc = Document()
    # We need one additional row for the table header
    table = doc.add_table(rows=len(excited_states)+1,
                          cols=len(header))

    # Set header in the first row
    for item, cell in zip(header, table.rows[0].cells):
        cell.text = item

    # Start from the 2nd row (index 1) and fill in all cells
    # with the parsed data.
    for i, fmt_list in enumerate(as_fmt_lists, 1):
        for item, cell in zip(fmt_list, table.rows[i].cells):
            cell.text = item
    # Save the document
    doc.save(docx_fn)


def as_tiddly_table(excited_states, verbose_mos):
    as_fmt_lists, header = as_table(excited_states,
                                    verbose_mos,
                                    newline_str="<br>")
    # Surround the numbers in S_i with ,, ,, so they are displayed
    # with subscript in tiddlywiki.
    header_line = "|! " + " |! ".join(header) + " |"
    data_lines = [
        "| " + " | ".join(exc_line) + " |" for exc_line
        in as_fmt_lists]
    print(header_line)
    for l in data_lines:
        print(l)


def as_theodore(excited_states, fn):
    """Combine the NTO-pictures generated by THEOdore/JMOl with
    the information parsed by td.py in a new .html-document."""
    # Search for  NTO-pictures in ./theodore
    pngs = [f for f in os.listdir("./theodore")
            if f.endswith(".png")]
    # Their naming follows the pattern
    # NTO{MO}{sym}_{pairNum}{o|v}_{weight}.png
    png_regex = "NTO(\d+)([\w'\"]+)_(\d+)(o|v)_([\d\.]+)\.png"
    parsed_png_fns = [re.match(png_regex, png).groups()
                      for png in pngs]
    # Convert the data to their proper types
    parsed_png_fns = [(int(state), irrep, int(pair), ov, float(weight))
                      for state, irrep, pair, ov, weight in parsed_png_fns]
    # Combine the data with the filenames of the .pngs
    zipped = [(p, *parsed_png_fns[i])
              for i, p in enumerate(pngs)]
    # Neglect NTOs below a certain threshold (20%)
    zipped = [nto for nto in zipped if nto[5] >= 0.2]
    # Sort by state, by weight, by pair  and by occupied vs. virtual
    zipped = sorted(zipped, key=lambda nto: (nto[2], nto[1], -nto[5],
                                             nto[3], nto[4]))
    assert((len(zipped) % 2) == 0)
    nto_dict = dict()
    for nto_pair in chunks(zipped, 2):
        onto, vnto = nto_pair
        assert(onto[2] == vnto[2])
        ofn, state, irrep, pair, ov, weight = onto
        vfn = vnto[0]
        key = (state, irrep)
        nto_dict.setdefault(key, list()).append((ofn, vfn, weight))

    j2_env = Environment(loader=FileSystemLoader(THIS_DIR,
                                                 followlinks=True))
    tpl = j2_env.get_template("templates/theo.tpl")
    # Only pass states to the template for which NTO pictures exist
    states = excited_states[:len(nto_dict)]
    """
    states = sorted(states, key=lambda es: -es.l)
    # Update the ids of the states
    for id, s in enumerate(states, 1):
        s.id_sorted = id
    """
    ren = tpl.render(states=states,
                     nto_dict=nto_dict,
                     fn=fn)
    with open("theo_comb.html", "w") as handle:
        handle.write(ren)


def is_orca(text):
    orca_re = "\* O   R   C   A \*"
    return re.search(orca_re, text)

def parse(fn):
    with open(fn) as handle:
        text = handle.read()

    # Parse outputs
    if args.file_name.endswith("escf.out"):
        # TURBOMOLE escf
        return turbo.parse_escf(text)
    elif args.file_name.endswith("ricc2.out"):
        # TURBOMOLE ricc2
        return turbo.parse_ricc2(text)
    elif is_orca(text):
        return orca.parse_tddft(text)
    else:
        # Gaussian
        return gaussian.parse_tddft(text)

    return excited_states


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
    parser.add_argument("--se", action="store_true",
                        help="Sort by energy.")
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
    """
    parser.add_argument("--csv",
                        help="Read csv file containing verbose MO-names.")
    """
    parser.add_argument("--summary", action="store_true",
                        help="Print summary to stdout.")
    parser.add_argument("--ci-coeff", dest="ci_coeff", type=float,
                        default=0.2, help="Only consider ci coefficients "
                        "not less than.")
    parser.add_argument("--spectrum", dest="spectrum", action="store_true",
                        help="Calculate the UV spectrum from the TD "
                        "calculation (FWHM = 0.4 eV).")
    parser.add_argument("--e2f", dest="e2f", action="store_true",
                        help="Used with spectrum. Converts the molecular "
                        "extinctions coefficients on the ordinate to "
                        "oscillator strengths.")
    """
    parser.add_argument("--hi", dest="highlight_impulses", type=int,
                        nargs="+", help="List of excitations. Their "
                        "oscillator strength bars will be printed separatly.")
    """
    parser.add_argument("--nnorm", dest="nnorm", default=False,
                        action="store_true",
                        help="Don't normalize the calculated spectrum.")
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
    parser.add_argument("--tiddly", action="store_true",
                        help="Output the parsed data in Tiddlywiki-table"
                        "format.")
    parser.add_argument("--theodore", action="store_true",
                        help="Output HTML with NTO-picture from THEOdore.")
    parser.add_argument("--nosym", action="store_true",
                        help="Assign all excited states to the 'a' irrep.")
    parser.add_argument("--plot", action="store_true",
                        help="Plot the spectrum with matplotlib.")

    # Use the argcomplete module for autocompletion if it's available
    if "argcomplete" in sys.modules:
        parser.add_argument("file_name", metavar="fn",
                            help="File to parse.").completer = gaussian_logs_completer
        argcomplete.autocomplete(parser)
    else:
        parser.add_argument("file_name", metavar="fn",
                            help="File to parse.")
    args = parser.parse_args()

    try:
        mo_data_fn = "mos.json"
        with open(mo_data_fn) as handle:
            json_data = json.load(handle)

        verbose_mos = dict()
        for key in json_data:
            mo, irrep = key.split()
            #mo = int(mo)
            verbose_mos[(mo, irrep)] = json_data[key]
    except IOError:
        logging.warning("Couldn't find verbose MO-names"
                        " in mos.json")
        verbose_mos = None


    excited_states = parse(args.file_name)
    for exc_state in excited_states:
        exc_state.calculate_contributions()
        exc_state.correct_backexcitations()
        exc_state.suppress_low_ci_coeffs(args.ci_coeff)
        exc_state.update_irreps()

    spectrum = Spectrum(excited_states)

    if args.nosym:
        for es in excited_states:
            es.spat = "a"
            es.irrep = "a"

    if args.only_first:
        excited_states = excited_states[:args.only_first]

    if args.spectrum:
        # Starting and ending wavelength of the spectrum to be calculated
        in_nm, osc_nm = spectrum.nm
        in_eV, osc_eV = spectrum.eV
        out_fns = ["nm.spec", "osc_nm.spec", "eV.spec", "osc_eV.spec"]
        for out_fn, spec in zip(out_fns, (in_nm, osc_nm, in_eV, osc_eV)):
            np.savetxt(out_fn, spec)
        gnuplot_tpl = os.path.join(THIS_DIR, "templates", "gnuplot.plt")
        shutil.copy(gnuplot_tpl, "gnuplot.plt")

    if args.plot:
        spectrum.plot_eV()

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
    if args.sf:
        excited_states = sorted(excited_states,
                                key=lambda exc_state: -exc_state.f)
    if args.se:
        excited_states = sorted(excited_states,
                                key=lambda es: -es.l)
    """
    # Sorting by energy is the default.
    else:
        excited_states = sorted(excited_states,
                                key=lambda exc_state: exc_state.dE)
        # Relabel the states from 1 to len(excited_states)
        for i, es in enumerate(excited_states, 1):
            es.id = i
    """
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

    # Find important irreps
    irreps = set(itertools.chain(*[exc_state.irreps for exc_state in
                                   excited_states]))
    min_max_mos = dict()
    for irrep in irreps:
        min_max_mos[irrep] = list()
    for exc_state in excited_states:
        for irrep in exc_state.irreps:
            min_max_mos[irrep].extend(exc_state.mo_trans_per_irrep[irrep])
    for irrep in min_max_mos:
        mos = set(min_max_mos[irrep])
        min_max_mos[irrep] = (min(mos), max(mos))

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
        make_docx(excited_states, verbose_mos)
        sys.exit()
    if args.tiddly:
        as_tiddly_table(excited_states, verbose_mos)
        sys.exit()
    if args.theodore:
        as_theodore(excited_states, args.file_name)
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
    for irrep in irreps:
        min_mo, max_mo = min_max_mos[irrep]
        print("Irrep {}: MOs {} - {}".format(irrep,
                                             min_mo,
                                             max_mo))
