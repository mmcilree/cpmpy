#!/usr/bin/env python
"""
    Interface to MiniZinc's Python API

    CPMpy can translate CPMpy models to the (text-based) MiniZinc language.

    MiniZinc is a free and open-source constraint modeling language.
    MiniZinc is used to model constraint satisfaction and optimization problems in
    a high-level, solver-independent way, taking advantage of a large library of
    pre-defined constraints. The model is then compiled into FlatZinc, a solver input
    language that is understood by a wide range of solvers.
    https://www.minizinc.org

    Documentation of the solver's own Python API:
    https://minizinc-python.readthedocs.io/

    ===============
    List of classes
    ===============

    .. autosummary::
        :nosignatures:

        CPM_minizinc
"""
import numpy as np
import sys
from datetime import timedelta # for mzn's timeout
from .solver_interface import SolverInterface, SolverStatus, ExitStatus
from ..expressions.core import Expression, Comparison, Operator
from ..expressions.variables import _NumVarImpl, _IntVarImpl, _BoolVarImpl, NegBoolView
from ..expressions.utils import is_num, is_any_list, flatlist
from ..transformations.get_variables import get_variables_model, get_variables

class CPM_minizinc(SolverInterface):
    """
    Interface to MiniZinc's Python API

    Requires that the 'minizinc' python package is installed:
    $ pip install minizinc
    
    as well as the MiniZinc bundled binary packages, downloadable from:
    https://www.minizinc.org/software.html

    See detailed installation instructions at:
    https://minizinc-python.readthedocs.io/en/latest/getting_started.html

    Note for Jupyter users: MiniZinc uses AsyncIO, so using it in a jupyter notebook gives
    you the following error: RuntimeError: asyncio.run() cannot be called from a running event loop
    You can overcome this by `pip install nest_asyncio`
    and adding in the top cell `import nest_asyncio; nest_asyncio.apply()`


    Creates the following attributes (see parent constructor for more):
    mzn_model: object, the minizinc.Model instance
    mzn_solve: object, the minizinc.Solver instance
    mzn_txt_solve: str, the 'solve' item in text form, so it can be overwritten
    """

    @staticmethod
    def supported():
        # try to import the package
        try:
            import minizinc
            return True
        except ImportError as e:
            return False

    @staticmethod
    def solvernames():
        """
            Returns solvers supported by MiniZinc on your system

            WARNING, some of them may not actually be installed on your system
            (namely cplex, gurobi, scip, xpress)
            the following are bundled in the bundle: chuffed, coin-bc, gecode
        """
        import minizinc
        import json
        # from minizinc.Solver.lookup()
        out = minizinc.default_driver.run(["--solvers-json"])
        out_lst = json.loads(out.stdout)

        solvers = []
        for s in out_lst:
            name = s["id"].split(".")[-1]
            if name not in ['findmus', 'gist', 'globalizer']:  # not actually solvers
                solvers.append(name)
        return solvers


    def __init__(self, cpm_model=None, subsolver=None):
        """
        Constructor of the native solver object

        Arguments:
        - cpm_model: Model(), a CPMpy Model() (optional)
        - subsolver: str, name of a subsolver (optional)
                          has to be one of solvernames()
        """
        if not self.supported():
            raise Exception("CPM_minizinc: Install the python package 'minizinc'")

        import minizinc

        # determine subsolver
        if subsolver is None or subsolver == 'minizinc':
            # default solver
            subsolver = "gecode"
        elif subsolver.startswith('minizinc:'):
            subsolver = subsolver[9:] # strip 'minizinc:'

        # initialise the native solver object
        # (so its params can still be changed before calling solve)
        self.mzn_solver = minizinc.Solver.lookup(subsolver)
        # It is the model object that contains the constraints for minizinc
        self.mzn_model = minizinc.Model()
        self.mzn_model.add_string("% Generated by CPMpy\ninclude \"globals.mzn\";\n\n")
        # Prepare solve statement, so it can be overwritten on demand
        self.mzn_txt_solve = "solve satisfy;"

        # initialise everything else and post the constraints/objective
        super().__init__(name="minizinc:"+subsolver, cpm_model=cpm_model)


    def _pre_solve(self, time_limit=None, **kwargs):
        """ shared by solve() and solveAll() """
        import minizinc

        if time_limit is not None:
            kwargs['timeout'] = timedelta(seconds=time_limit)

        # hack, we need to add the objective in a way that it can be changed
        # later, so make copy of the mzn_model
        copy_model = self.mzn_model.__copy__() # it is implemented
        copy_model.add_string(self.mzn_txt_solve)
        # Transform Model into an instance
        mzn_inst = minizinc.Instance(self.mzn_solver, copy_model)

        kwargs['output-time'] = True # required for time getting
        return (kwargs, mzn_inst)

    def solve(self, time_limit=None, **kwargs):
        """
            Call the MiniZinc solver
            
            Creates and calls an Instance with the already created mzn_model and mzn_solver

            Arguments:
            - time_limit:  maximum solve time in seconds (float, optional)
            - kwargs:      any keyword argument, sets parameters of solver object

            Arguments that correspond to solver parameters:
                - free_search=True              Allow the solver to ignore the search definition within the instance. (Only available when the -f flag is supported by the solver). (Default: 0)
                - optimisation_level=0          Set the MiniZinc compiler optimisation level. (Default: 1; 0=none, 1=single pass, 2=double pass, 3=root node prop, 4,5=probing)
                - ...                           I am not sure where solver-specific arguments are documented, but the docs say that command line arguments can be passed by ommitting the '-' (e.g. 'f' instead of '-f')?
            The minizinc solver parameters are partly defined in its API:
            https://minizinc-python.readthedocs.io/en/latest/api.html#minizinc.instance.Instance.solve

            Does not store the minizinc.Instance() or minizinc.Result()
        """
        # make mzn_inst
        (mzn_kwargs, mzn_inst) = self._pre_solve(time_limit=time_limit, **kwargs)
        
        # call the solver, with parameters
        mzn_result = mzn_inst.solve(**mzn_kwargs)

        # new status, translate runtime
        self.cpm_status = self._post_solve(mzn_result)

        # True/False depending on self.cpm_status
        has_sol = self._solve_return(self.cpm_status)

        # translate solution values (of user specified variables only)
        self.objective_value_ = None
        if has_sol: #mzn_result.status.has_solution():
            mznsol = mzn_result.solution
            if is_any_list(mznsol):
                print("Warning: multiple solutions found, only returning last one")
                mznsol = mznsol[-1]

            # fill in variable values
            for cpm_var in self.user_vars:
                sol_var = self.solver_var(cpm_var)
                if hasattr(mznsol, sol_var):
                    cpm_var._value = getattr(mznsol, sol_var)
                else:
                    print("Warning, no value for ", sol_var)

            # translate objective, for optimisation problems only (otherwise None)
            self.objective_value_ = mzn_result.objective
        
        return has_sol

    def _post_solve(self, mzn_result):
        """ shared by solve() and solveAll() """
        import minizinc

        # new status, translate runtime
        self.cpm_status = SolverStatus(self.name)
        if 'time' in mzn_result.statistics:
            self.cpm_status.runtime = mzn_result.statistics['time']  # --output-time

        # translate exit status
        mzn_status = mzn_result.status
        if mzn_status == minizinc.result.Status.SATISFIED:
            self.cpm_status.exitstatus = ExitStatus.FEASIBLE
        elif mzn_status == minizinc.result.Status.ALL_SOLUTIONS:
            self.cpm_status.exitstatus = ExitStatus.FEASIBLE
        elif mzn_status == minizinc.result.Status.OPTIMAL_SOLUTION:
            self.cpm_status.exitstatus = ExitStatus.OPTIMAL
        elif mzn_status == minizinc.result.Status.UNSATISFIABLE:
            self.cpm_status.exitstatus = ExitStatus.UNSATISFIABLE
        elif mzn_status == minizinc.result.Status.ERROR:
            self.cpm_status.exitstatus = ExitStatus.ERROR
            raise Exception("MiniZinc solver returned with status 'Error'")
        elif mzn_status == minizinc.result.Status.UNKNOWN:
            # means, no solution was found (e.g. within timeout?)...
            self.cpm_status.exitstatus = ExitStatus.ERROR
        else:
            raise NotImplementedError  # a new status type was introduced, please report on github

        return self.cpm_status

    async def _solveAll(self, display=None, time_limit=None, solution_limit=None, **kwargs):
        """ Special 'async' function because mzn.solutions() is async """
        # make mzn_inst
        (kwargs, mzn_inst) = self._pre_solve(time_limit=time_limit, **kwargs)
        kwargs['all_solutions'] = True

        solution_count = 0
        # has an asynchronous generator
        async for mzn_result in mzn_inst.solutions(**kwargs):
            # was the last one
            if mzn_result.solution is None:
                break

            # display (and reverse-map first) if needed
            if display is not None:
                mznsol = mzn_result.solution

                # fill in variable values
                for cpm_var in self.user_vars:
                    sol_var = self.solver_var(cpm_var)
                    if hasattr(mznsol, sol_var):
                        cpm_var._value = getattr(mznsol, sol_var)
                    else:
                        print("Warning, no value for ", sol_var)

                # and the actual displaying
                if isinstance(display, Expression):
                    print(display.value())
                elif isinstance(display, list):
                    print([v.value() for v in display])
                else:
                    display() # callback

            # count and stop
            solution_count += 1
            if solution_count == solution_limit:
                break

            # add nogood on the user variables
            self += any([v != v.value() for v in self.user_vars])

        # status handling
        self._post_solve(mzn_result)

        return solution_count


    def solver_var(self, cpm_var) -> str:
        """
            Creates solver variable for cpmpy variable
            or returns from cache if previously created

            Returns minizinc-friendly 'string' name of var

            XXX WARNING, this assumes it is never given a 'NegBoolView'
            might not be true... e.g. in revar after solve?
        """
        if is_num(cpm_var):
            return str(cpm_var)

        if cpm_var not in self._varmap:
            # clean the varname
            varname = cpm_var.name
            mzn_var = varname.replace(',', '_').replace('.', '_').replace(' ', '_').replace('[', '_').replace(']', '')

            if isinstance(cpm_var, _BoolVarImpl):
                self.mzn_model.add_string(f"var bool: {mzn_var};\n")
            elif isinstance(cpm_var, _IntVarImpl):
                self.mzn_model.add_string(f"var {cpm_var.lb}..{cpm_var.ub}: {mzn_var};\n")
            self._varmap[cpm_var] = mzn_var

        return self._varmap[cpm_var]


    def objective(self, expr, minimize):
        """
            Post the given expression to the solver as objective to minimize/maximize

            - expr: Expression, the CPMpy expression that represents the objective function
            - minimize: Bool, whether it is a minimization problem (True) or maximization problem (False)

            'objective()' can be called multiple times, only the last one is stored
        """
        self.user_vars.update(get_variables(expr)) # add objvars to vars

        # make objective function or variable and post
        obj = self._convert_expression(expr)
        # do not add it to the mzn_model yet, supports only one 'solve' entry
        if minimize:
            self.mzn_txt_solve = "solve minimize {};\n".format(obj)
        else:
            self.mzn_txt_solve = "solve maximize {};\n".format(obj)


    def __add__(self, cpm_con):
        """
        Post a (list of) CPMpy constraints(=expressions) to the solver

        :param cpm_con CPMpy constraint, or list thereof
        :type cpm_con (list of) Expression(s)
        """
        # add new user vars to the set
        self.user_vars.update(get_variables(cpm_con))

        # we can't unpack lists in _post_constraint, so must do it upfront
        # and can't make assumptions on '.flat' existing either
        if is_any_list(cpm_con):
            cpm_con = flatlist(cpm_con)
        else:
            cpm_con = [cpm_con]

        # no transformations
        for con in cpm_con:
            self._post_constraint(con)

        return self

    def _post_constraint(self, cpm_expr):
        """
            Post a CPMpy constraint to the native solver API
        """
        # Get text expression, add to the solver
        txt_cons = f"constraint {self._convert_expression(cpm_expr)};\n"
        self.mzn_model.add_string(txt_cons)

    def _convert_expression(self, expr) -> str:
        """
            Convert a CPMpy expression into a minizinc-compatible string

            recursive: also converts nested subexpressions, so we need a
            function that returns strings
        """
        if is_any_list(expr):
            if len(expr) == 1:
                # unary special case, don't put in list
                # continue with later code
                expr = expr[0]
            else:
                if isinstance(expr, np.ndarray):
                    # must flatten
                    expr_str = [self._convert_expression(e) for e in expr.flat]
                else:
                    expr_str = [self._convert_expression(e) for e in expr]
                return "[{}]".format(",".join(expr_str))

        if not isinstance(expr, Expression) or \
                isinstance(expr, _NumVarImpl):
            if expr is True:
                return "true"
            if expr is False:
                return "false"
            # default
            if isinstance(expr, NegBoolView):
                return "not " + self.solver_var(expr._bv)
            return self.solver_var(expr)

        # table(vars, tbl): no [] nesting of args, and special table output...
        if expr.name == "table":
            str_vars = self._convert_expression(expr.args[0])
            str_tbl = "[|\n"  # opening
            for row in expr.args[1]:
                str_tbl += ",".join(map(str, row)) + " |"  # rows
            str_tbl += "\n|]"  # closing
            return "table({}, {})".format(str_vars, str_tbl)

        args_str = [self._convert_expression(e) for e in expr.args]

        # standard expressions: comparison, operator, element
        if isinstance(expr, Comparison):
            # wrap args that are a subexpression in ()
            for i, arg_str in enumerate(args_str):
                if isinstance(expr.args[i], Expression): #(Comparison, Operator)
                    args_str[i] = "(" + args_str[i] + ")"
            # infix notation
            return "{} {} {}".format(args_str[0], expr.name, args_str[1])

        elif isinstance(expr, Operator):
            # some names differently (the infix names!)
            printmap = {'and': '/\\', 'or': '\\/',
                        'sum': '+', 'sub': '-',
                        'mul': '*', 'pow': '^'}
            op_str = expr.name
            if op_str in printmap:
                op_str = printmap[op_str]

            # TODO: pretty printing of () as in Operator?

            # special case: unary -
            if self.name == '-':
                return "-{}".format(args_str[0])

            # very special case: weighted sum (before 2-ary)
            if expr.name == 'wsum':
                # I don't think there is a more direct way unfortunately
                w = [self._convert_expression(wi) for wi in expr.args[0]]
                x = [self._convert_expression(xi) for xi in expr.args[1]]
                args_str = [f"{wi}*{xi}" for wi,xi in zip(w,x)]
                return "{}([{}])".format("sum", ",".join(args_str))

            # special case, infix: two args
            if len(args_str) == 2:
                # wrap args that are a subexpression in ()
                for i, arg_str in enumerate(args_str):
                    if isinstance(expr.args[i], Expression):
                        args_str[i] = "(" + args_str[i] + ")"
                return "{} {} {}".format(args_str[0], op_str, args_str[1])

            # special case: n-ary (non-binary): rename operator
            printnary = {'and': 'forall', 'or': 'exists', 'sum': 'sum'}
            if expr.name in printnary:
                op_str = printnary[expr.name]
                return "{}([{}])".format(op_str, ",".join(args_str))

            # default: prefix printing
            return "{}({})".format(op_str, ",".join(args_str))

        elif expr.name == "element":
            subtype = "int"
            if all(isinstance(v, bool) or \
                   (isinstance(v, Expression) and v.is_bool()) \
                   for v in expr.args[0]):
                subtype = "bool"
            idx = args_str[1]
            # minizinc is offset 1, which can be problematic for element...
            # thx Hakan, fix by using array1d(0..len, []), issue #54
            txt = "\n    let {{ array[int] of var {}: arr=array1d({}..{},{}) }} in\n".format(subtype, 0,
                                                                                             len(expr.args[0]) - 1,
                                                                                             args_str[0])
            txt += f"      arr[{idx}]"
            return txt

        # rest: global constraints
        elif expr.name.endswith('circuit'):  # circuit, subcircuit
            # minizinc is offset 1, which can be problematic here...
            if any(isinstance(e, _IntVarImpl) and e.lb == 0 for e in expr.args):
                # redo args_str[0]
                args_str = ["{}+1".format(self._convert_expression(e)) for e in expr.args]

        print_map = {"allequal":"all_equal", "xor":"xorall"}
        if expr.name in print_map:
            return "{}([{}])".format(print_map[expr.name], ",".join(args_str))

        # default (incl name-compatible global constraints...)
        return "{}([{}])".format(expr.name, ",".join(args_str))

    def solveAll(self, display=None, time_limit=None, solution_limit=None, **kwargs):
        """
            Compute all solutions and optionally display the solutions.

            MiniZinc-specific implementation

            Arguments:
                - display: either a list of CPMpy expressions, OR a callback function, called with the variables after value-mapping
                        default/None: nothing displayed
                - time_limit: stop after this many seconds (default: None)
                - solution_limit: stop after this many solutions (default: None)
                - any other keyword argument

            Returns: number of solutions found
        """
        # XXX: check that no objective function??
        import asyncio

        # HAD TO DEFINE OUR OWN ASYNC HANDLER
        coroutine = self._solveAll(display=display, time_limit=time_limit,
                                    solution_limit=solution_limit, **kwargs)
        # THE FOLLOWING IS STRAIGHT FROM `minizinc.instance.solve()`
        # LETS HOPE IT DOES NOT DIVERGE FROM UPSTREAM
        if sys.version_info >= (3, 7):
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(coroutine)
        else:
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.events.new_event_loop()

            try:
                asyncio.events.set_event_loop(loop)
                return loop.run_until_complete(coroutine)
            finally:
                asyncio.events.set_event_loop(None)
                loop.close()
