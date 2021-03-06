#!/usr/bin/env python

import itertools
import sys

from parser import (parse_string, Flag, Implication, AllOfOperator,
        AnyOfOperator, ExactlyOneOfOperator, AtMostOneOfOperator,
        NaryOperator, parse_immutables)
from sort_nary import immutability_sort, sort_nary
from to_flat3 import flatten3
from validate_ast import validate_ast_passthrough


def validate_constraint(flags, constraint, condition_cache=None):
    if condition_cache is None:
        condition_cache = {}

    for expr in constraint:
        if isinstance(expr, Flag):
            # cache results for flags used in the implication conditions
            # by their instances to account for 'common prefixes' when
            # using the implication form
            if not condition_cache.setdefault(id(expr),
                    flags[expr.name] == expr.enabled):
                return False
        elif isinstance(expr, Implication):
            if validate_constraint(flags, expr.condition, condition_cache):
                if not validate_constraint(flags, expr.constraint):
                    return False
        elif isinstance(expr, AnyOfOperator):
            if not any(validate_constraint(flags, [x]) for x in expr.constraint):
                return False
        elif isinstance(expr, ExactlyOneOfOperator):
            if list(validate_constraint(flags, [x]) for x in expr.constraint).count(True) != 1:
                return False
        elif isinstance(expr, AtMostOneOfOperator):
            if list(validate_constraint(flags, [x]) for x in expr.constraint).count(True) > 1:
                return False
        elif isinstance(expr, AllOfOperator):
            if not all(validate_constraint(flags, [x]) for x in expr.constraint):
                return False
        else:
            raise ValueError('Unknown AST expr: %s' % expr)

    return True


class ImmutabilityError(Exception):
    def __init__(self, flag_name):
        super(ImmutabilityError, self).__init__('Immutability error: value of %s mismatches' % flag_name)
        self.flag_name = flag_name


class InfiniteLoopError(Exception):
    def __init__(self):
        super(InfiniteLoopError, self).__init__('Constraints cause infinite loop')


def apply_solving(flags, constraint, immutable_flags, negate=False, condition_cache=None):
    if condition_cache is None:
        condition_cache = {}

    for expr in constraint:
        if isinstance(expr, Flag):
            want = expr.enabled
            if negate:
                want = not want
            if immutable_flags.get(expr.name, want) != want:
                raise ImmutabilityError(expr.name)
            flags[expr.name] = want
        elif isinstance(expr, Implication):
            if validate_constraint(flags, expr.condition, condition_cache):
                apply_solving(flags, expr.constraint, immutable_flags, negate, condition_cache)
        elif isinstance(expr, AnyOfOperator):
            if not validate_constraint(flags, [expr]):
                apply_solving(flags, expr.constraint[0:1], immutable_flags, negate)
        elif isinstance(expr, ExactlyOneOfOperator):
            apply_solving(flags, [AnyOfOperator(expr.constraint)], immutable_flags, negate)
            apply_solving(flags, [AtMostOneOfOperator(expr.constraint)], immutable_flags, negate)
        elif isinstance(expr, AtMostOneOfOperator):
            if not validate_constraint(flags, [expr]):
                past_first = False
                for x in expr.constraint:
                    if validate_constraint(flags, [x]):
                        if past_first:
                            apply_solving(flags, [x], immutable_flags, not negate)
                        else:
                            past_first = True
        elif isinstance(expr, AllOfOperator):
            if not negate:
                apply_solving(flags, expr.constraint, immutable_flags, negate)
            else:
                apply_solving(flags, expr.constraint[0:1], immutable_flags, negate)
        else:
            raise ValueError('Unknown AST expr: %s' % expr)


def get_all_flags(ast):
    for expr in ast:
        if isinstance(expr, Flag):
            yield expr
            continue
        if isinstance(expr, Implication):
            for x in get_all_flags(expr.condition):
                yield x
        for x in get_all_flags(expr.constraint):
            yield x


def do_solving(sorted_flags, inp_flags, ast, immutable_flags, verbose=True):
    prev_states = [inp_flags]
    out_flags = dict(inp_flags)
    # allow up to 1000 iterations
    for i in range(1, 1000):
        try:
            try:
                apply_solving(out_flags, ast, immutable_flags)
            except ImmutabilityError as e:
                if verbose:
                    print('\033[31m [unsolvable: immutable %s mismatched]' % e.flag_name, end='')
                raise
            else:
                valid_now = validate_constraint(out_flags, ast)
                if verbose:
                    if valid_now:
                        print('\033[32m', end='')
                    else:
                        print('\033[33m', end='')
                    for f in sorted_flags:
                        if out_flags[f] != prev_states[-1][f]:
                            print(' \033[1m%d\033[22m' % out_flags[f], end='')
                        else:
                            print(' %d' % out_flags[f], end='')

                if not valid_now:
                    # compare with previous states
                    for x in prev_states:
                        if out_flags == x:
                            if verbose:
                                print('\033[31m [unsolvable due to loop]', end='')
                            raise InfiniteLoopError()
        finally:
            if verbose:
                print('\033[0m')

        if valid_now:
            return (out_flags, i)

        prev_states.append(dict(out_flags))
        if verbose:
            print('%*s |' % (len(sorted_flags) * 2, ''), end='')


def print_solutions(constraint_str, immutable_str):
    # sort n-ary expressions
    immutable_flags = parse_immutables(immutable_str)
    ast = sort_nary(validate_ast_passthrough(parse_string(constraint_str)),
            immutability_sort(immutable_str))
    ast = list(ast)
    print(ast)
    print()

    # implication variant
    impl_ast = []
    for c, e in flatten3(ast):
        if c:
            e = Implication(c, [e])
        impl_ast.append(e)

    all_flags = frozenset(x.name for x in get_all_flags(ast))

    # print flag names, vertically
    sorted_flags = sorted(all_flags)
    no_flags = len(sorted_flags)
    y_max = max(len(x) for x in sorted_flags)
    for y in range(0, y_max):
        for f in sorted_flags + ['|'] + sorted_flags:
            print(' %s' % (f[len(f)-y_max+y] if y >= y_max - len(f) else ' '), end='')
        print('')

    # solve for input = 000... to 111...
    max_iters = 0
    unsolvable = 0
    mismatched_solutions = 0
    for values in itertools.product((False, True), repeat=no_flags):
        inp_flags = dict(zip(sorted_flags, values))

        skip = False
        for k, v in immutable_flags.items():
            # skip mismatches for immutables
            if inp_flags[k] != v:
                skip = True
                break
        if skip:
            continue

        for f in sorted_flags:
            if f in immutable_flags:
                print('\033[33m', end='')
            print(' %d' % inp_flags[f], end='')
            if f in immutable_flags:
                print('\033[0m', end='')
        print(' |', end='')

        if validate_constraint(inp_flags, ast):
            print('\033[32m', end='')
            for f in sorted_flags:
                print(' %d' % inp_flags[f], end='')
            print(' (==)\033[0m')
        else:
            try:
                ret, iters = do_solving(sorted_flags, inp_flags, ast, immutable_flags)
            except (ImmutabilityError, InfiniteLoopError):
                unsolvable += 1
            else:
                if iters > max_iters:
                    max_iters = iters

                ret_impl, ret_iters = do_solving(sorted_flags, inp_flags,
                        impl_ast, immutable_flags, verbose=False)
                if ret != ret_impl:
                    mismatched_solutions += 1
                    print('%*s |\033[31m' % (len(sorted_flags) * 2, ''), end='')
                    for f in sorted_flags:
                        if ret_impl[f] != ret[f]:
                            print(' \033[1m%d\033[22m' % ret_impl[f], end='')
                        else:
                            print(' %d' % ret_impl[f], end='')
                    print(' [mismatch between implication and basic form]\033[0m')

    print()
    print('max iterations: %d;  unsolvable: %d;  mismatched solutions for transform: %d'
            % (max_iters, unsolvable, mismatched_solutions))



def main(constraint_str, immutable_flag_str=''):
    print_solutions(constraint_str, immutable_flag_str)


if __name__ == '__main__':
    main(*sys.argv[1:])
