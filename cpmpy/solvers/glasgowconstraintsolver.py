#!/usr/bin/env python
"""
    Interface to the Glasgow Constraint Solver's API for the cpmpy library.
    The key feature of this solver is the ability to produce proof logs.
    ===============
    List of classes
    ===============
    .. autosummary::
        :nosignatures:
        GlasgowConstraintSolver
"""
from cpmpy.transformations.comparison import only_numexpr_equality
from cpmpy.transformations.reification import only_bv_implies, reify_rewrite
from .solver_interface import SolverInterface, SolverStatus, ExitStatus
from ..expressions.core import Expression, Comparison, Operator
from ..expressions.variables import _BoolVarImpl, _IntVarImpl, _NumVarImpl, NegBoolView
from ..expressions.utils import is_num, is_any_list
from ..transformations.get_variables import get_variables
from ..transformations.flatten_model import flatten_constraint, flatten_objective, get_or_make_var

class CPM_glasgowconstraintsolver(SolverInterface):
    """
    Interface to Glasgow Constraint Solver's API
    Requires that the 'gcspy' python package is installed:
    <no pip package or installation instructions as yet>
    """

    @staticmethod
    def supported():
        # try to import the package
        try:
            import gcspy
            return True
        except ImportError as e:
            return False

    def __init__(self, cpm_model=None, subsolver=None):
        """
        Constructor of the native solver object
        Arguments:
        - cpm_model: Model(), a CPMpy Model() (optional)
        - subsolver: str, name of a subsolver (optional)
        """
        if not self.supported():
            raise Exception("Glasgow Constraint Solver: Install the python package 'gcspy'")

        import gcspy

        assert(subsolver is None) # unless you support subsolvers, see pysat or minizinc

        # initialise the native solver object
        self.gcs = gcspy.GlasgowConstraintSolver()
        self.has_objective = False
        self.objective_var = None

        # initialise everything else and post the constraints/objective
        super().__init__(name="Glasgow Constraint Solver", cpm_model=cpm_model)

    def solve(self, time_limit=None, **kwargs):
        """
            Call the Glasgow Constraint Solver
            Arguments:
            - time_limit:  #TODO maximum solve time in seconds (float, optional)
            - kwargs:      any keyword argument, sets parameters of solver object
            Arguments that correspond to solver parameters:
            #TODO document solver parameters (there are none at the moment)
            #TODO <Add link to documentation of all solver parameters>
        """

        if time_limit is not None:
            raise NotImplementedError("Glasgow Constraint Solver does not currently support timeouts.")

        # call the solver, with parameters
        gcs_stats = self.gcs.solve(**kwargs)

        # new status, translate runtime
        self.cpm_status = SolverStatus(self.name)
        # self.cpm_status.runtime = self.gcs.time()

        # translate exit status
        if gcs_stats["solutions"] != 0:
            self.cpm_status.exitstatus = ExitStatus.FEASIBLE
        else:
            self.cpm_status.exitstatus = ExitStatus.UNSATISFIABLE

        # True/False depending on self.cpm_status
        has_sol = self._solve_return(self.cpm_status)

        # translate solution values (of user specified variables only)
        self.objective_value_ = None
        if has_sol:
            # fill in variable values
            for cpm_var in self.user_vars:
                sol_var = self.solver_var(cpm_var)
                cpm_var._value = self.gcs.get_solution_value(sol_var)

            # translate objective, for optimisation problems only
            if self.has_objective:
                self.objective_value_ = self.gcs.get_solution_value(self.objective_var)

        return has_sol

    def solver_var(self, cpm_var):
        """
            Creates solver variable for cpmpy variable
            or returns from cache if previously created
        """
        if is_num(cpm_var): # shortcut, eases posting constraints
            return self.gcs.create_integer_constant(cpm_var)

        # special case, negative-bool-view
        # work directly on var inside the view
        if isinstance(cpm_var, NegBoolView):
            # gcs only works with integer variables, so not(x) = -x + 1
            return self.gcs.add_constant(self.gcs.negate(self.solver_var(cpm_var._bv)), 1)

        # create if it does not exit
        if cpm_var not in self._varmap:
            if isinstance(cpm_var, _BoolVarImpl):
                # Bool vars are just int vars with 0, 1 domain
                revar = self.gcs.create_integer_variable([0, 1], str(cpm_var))
            elif isinstance(cpm_var, _IntVarImpl):
                # Note: range(lb, ub + 1) = [lb, lb+1, ..., ub-1, ub]
                revar = self.gcs.create_integer_variable(range(cpm_var.lb, cpm_var.ub + 1), str(cpm_var))
            else:
                raise NotImplementedError("Not a known var {}".format(cpm_var))
            self._varmap[cpm_var] = revar

        return self._varmap[cpm_var]

    def objective(self, expr, minimize=True):
        """
            Post the given expression to the solver as objective to minimize/maximize
            'objective()' can be called multiple times, only the last one is stored
            (technical side note: any constraints created during conversion of the objective
            are permanently posted to the solver)
        """
        # make objective function non-nested
        (flat_obj, flat_cons) = flatten_objective(expr)
        self += flat_cons # add potentially created constraints
        self.user_vars.update(get_variables(flat_obj)) # add objvars to vars

        # make objective function or variable and post
        obj = self._make_numexpr(flat_obj)
        self.has_objective = True
        self.objective_var = obj
        if minimize:
            self.gcs.minimise(obj)
            
        else:
            self.gcs.maximise(obj)

    def _make_numexpr(self, cpm_expr):
        """
            Turns a numeric CPMpy 'flat' expression into a solver-specific
            numeric expression
            Used especially to post an expression as objective function
        """
        if is_num(cpm_expr):
            return cpm_expr

        # decision variables, check in varmap
        if isinstance(cpm_expr, _NumVarImpl):  # _BoolVarImpl is subclass of _NumVarImpl
            return self.solver_var(cpm_expr)

        (obj_var, obj_cons) = get_or_make_var(cpm_expr)
        self += obj_cons
        return self.solver_var(obj_var)

    def __add__(self, cpm_con):
        """
        Post a (list of) CPMpy constraints(=expressions) to the solver
        Note that we don't store the constraints in a cpm_model,
        we first transform the constraints into primitive constraints,
        then post those primitive constraints directly to the native solver
        :param cpm_con CPMpy constraint, or list thereof
        :type cpm_con (list of) Expression(s)
        """
        # add new user vars to the set
        self.user_vars.update(get_variables(cpm_con))

        # apply transformations, then post internally
        cpm_cons = flatten_constraint(cpm_con)

        # Only less than and equals are fully reifiable.
        cpm_cons = reify_rewrite(cpm_cons, supported=frozenset(["=="]))
        cpm_cons = only_numexpr_equality(cpm_cons)
        cpm_cons = only_bv_implies(cpm_cons)

        for con in cpm_cons:
            self._post_constraint(con)

        return self

    def _post_constraint(self, cpm_expr, reify=False):
        """
            Post a primitive CPMpy constraint to the native solver API
            What 'primitive' means depends on the solver capabilities,
            more specifically on the transformations applied in `__add__()`
            Solvers do not need to support all constraints.
        """
        if isinstance(cpm_expr, _BoolVarImpl):
            # base case, just var or ~var
            self.gcs.post_or([self.solver_var(cpm_expr)])
        elif isinstance(cpm_expr, Operator):
            # 'and'/n, 'or'/n, 'xor'/n, '->'/2
            if cpm_expr.name == 'and':
                return self.gcs.post_and(self.solver_vars(cpm_expr.args))
            elif cpm_expr.name == 'or':
                return self.gcs.post_or(self.solver_vars(cpm_expr.args))
            
            # Part-Reified constraint: Var -> Boolexpr
            elif cpm_expr.name == '->':
                assert(isinstance(cpm_expr.args[0], _BoolVarImpl))  # lhs must be boolvar due to only_bv_implies
                bool_lhs = cpm_expr.args[0]
                reif_var = self.solver_var(bool_lhs)
                bool_expr = cpm_expr.args[1]
                if isinstance(bool_expr, _BoolVarImpl): # bv1 -> bv2
                    return self.gcs.post_implies(self.solver_vars([bool_lhs, bool_expr]))
                if isinstance(bool_expr, Operator): # bv -> and(...), bv -> or(...)  # not sure about ('xor' and '->' ???)
                    if bool_expr.name == 'and':
                        return self.gcs.post_and_if(self.solver_vars(bool_expr.args), reif_var)
                    elif bool_expr.name == 'or':
                        return self.gcs.post_or_if(self.solver_vars(bool_expr.args), reif_var)
                    elif bool_expr.name == '->':
                        return self.gcs.post_implies_if(self.solver_vars(bool_expr.args), reif_var)
                    elif bool_expr.name == 'xor': # OR-tools implementation doesn't seem to deal with this case?
                        return self.gcs.post_xor_if(self.solver_vars(bool_expr.args), reif_var)
                    else:
                        # Shouldn't happen if reify_rewrite worked?
                        raise NotImplementedError("Not currently supported by Glasgow Constraint Solver API '{}' {}".format)
                elif isinstance(bool_expr, Comparison):
                    lhs = bool_expr.args[0]
                    rhs = bool_expr.args[1]
                    if bool_expr.name == '==':
                        return self.gcs.post_equals_if(*self.solver_vars([lhs, rhs]), reif_var)
                    # elif cpm_expr.name == '!=':
                    #     return self.gcs.post_not_equals_if(*self.solver_vars(lhs.args + [rhs]), reif_var)
                    elif bool_expr.name == '<=':
                        return self.gcs.post_compare_less_if(*self.solver_vars([lhs, rhs]), reif_var, True)
                    elif bool_expr.name == '<':
                        return self.gcs.post_compare_less_if(*self.solver_vars([lhs, rhs]), reif_var, False)
                    elif bool_expr.name == '>=':
                        return self.gcs.post_compare_less_if(*self.solver_vars([rhs, lhs]), reif_var, True)
                    elif bool_expr.name == '>':
                        return self.gcs.post_compare_less_if(*self.solver_vars([rhs, lhs]), reif_var, False)
                    else:
                        raise NotImplementedError("Not currently supported by Glasgow Constraint Solver API '{}' {}".format)
                else:
                    # Shouldn't happen if reify_rewrite worked
                    raise NotImplementedError("Not currently supported by Glasgow Constraint Solver API '{}' {}".format)
                    

        elif isinstance(cpm_expr, Comparison):
            lhs = cpm_expr.args[0]
            rhs = cpm_expr.args[1]
            if isinstance(lhs, _NumVarImpl):
                assert(isinstance(rhs, _NumVarImpl) or is_num(rhs))
                if cpm_expr.name == '==':
                    return self.gcs.post_equals(*self.solver_vars([lhs, rhs]))
                elif cpm_expr.name == '!=':
                    return self.gcs.post_not_equals(*self.solver_vars(lhs.args + [rhs]))
                elif cpm_expr.name == '<=':
                    return self.gcs.post_compare_less(*self.solver_vars(lhs.args + [rhs]), True)
                elif cpm_expr.name == '<':
                    return self.gcs.post_compare_less(*self.solver_vars(lhs.args + [rhs]), False)
                elif cpm_expr.name == '>=':
                    return self.gcs.post_compare_less(*self.solver_vars(lhs.args[::-1] + [rhs]), True)
                elif cpm_expr.name == '>':
                    return self.gcs.post_compare_less(*self.solver_vars(lhs.args[::-1] + [rhs]), False)
                else:
                    raise NotImplementedError("Not currently supported by Glasgow Constraint Solver API '{}'".format(cpm_expr))
            elif cpm_expr.name == '==':
                if lhs.name == "abs":
                    return self.gcs.post_abs(*self.solver_vars(lhs.args + [rhs]))
                elif lhs.name in ['mul', 'div', 'pow', 'mod', 'abs']:
                    return self.gcs.post_arithmetic(*self.solver_vars(lhs.args + [rhs]), lhs.name)
                elif lhs.name == "sub":
                    var1 = self.solver_var(lhs.args[0])
                    nVar2 = self.gcs.negate(self.solver_var(lhs.args[1]))
                    return self.gcs.post_arithmetic(var1, nVar2, self.solver_var(rhs), 'sum')
                elif lhs.name == "sum" and len(lhs.args) == 2:
                    var1 = self.solver_var(lhs.args[0])
                    var2 = self.solver_var(lhs.args[1])
                    return self.gcs.post_arithmetic(var1, var2, self.solver_var(rhs), 'sum')
                elif lhs.name == "sum" and len(lhs.args) > 2:
                    summands = self.solver_vars(lhs.args)
                    summands.append(self.gcs.negate(self.solver_var(rhs)))
                    return self.gcs.post_linear_equality(summands, [1]*len(summands), 0)
                elif lhs.name == "wsum":
                    summands = self.solver_vars(lhs.args[1])
                    summands.append(self.gcs.negate(self.solver_var(rhs)))
                    return self.gcs.post_linear_equality(summands, lhs.args[0] + [1], 0)
            else:
                raise NotImplementedError("Not currently supported by Glasgow Constraint Solver API '{}'".format(cpm_expr))
        # rest: base (Boolean) global constraints
        elif cpm_expr.name == 'alldifferent':
            return self.gcs.post_alldifferent(self.solver_vars(cpm_expr.args))
        else:
            self += cpm_expr.decompose()  # assumes a decomposition exists...
            return None # will throw error if used in reification

        raise NotImplementedError(cpm_expr)

