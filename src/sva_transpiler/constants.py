# constants.py
#
# INCLUDE: Definizioni operatori e System Functions SVA.
# FIX: Aggiunti operatori mancanti (Bitwise Reduction, Arithmetic Shift, Wildcard Eq, ecc.)

OP_MAP = {
    # Operatori Standard
    'Equality': '==',
    'Inequality': '!=',
    'LogicalAnd': '&&',
    'LogicalOr': '||',
    'BinaryAnd': '&',
    'BinaryOr': '|',
    'BinaryXor': '^',
    'Add': '+',
    'Subtract': '-',
    'Multiply': '*',
    'Divide': '/',
    'Mod': '%',
    'GreaterThan': '>',
    'LessThan': '<',
    'GreaterThanEqual': '>=',
    'LessThanEqual': '<=',
    'LogicalShiftLeft': '<<',
    'LogicalShiftRight': '>>',
    
    # Operatori Aggiunti (da OperatorExpressions.cpp)
    'BinaryXnor': '~^',
    'ArithmeticShiftLeft': '<<<',
    'ArithmeticShiftRight': '>>>',
    'Power': '**',
    'CaseEquality': '===',
    'CaseInequality': '!==',
    'WildcardEquality': '==?',
    'WildcardInequality': '!=?',
    'LogicalImplication': '->',     # Implicazione booleana (diversa da SVA |->)
    'LogicalEquivalence': '<->',
}

UNARY_MAP = {
    # Operatori Standard
    'LogicalNot': '!',
    'BitwiseNot': '~',
    'Plus': '+',
    'Minus': '-',
    
    # Operatori Aggiunti (Riduzione & Incremento)
    'BitwiseAnd': '&',      # Reduction AND
    'BitwiseOr': '|',       # Reduction OR
    'BitwiseXor': '^',      # Reduction XOR
    'BitwiseNand': '~&',    # Reduction NAND
    'BitwiseNor': '~|',     # Reduction NOR
    'BitwiseXnor': '~^',    # Reduction XNOR
    'Preincrement': '++',
    'Predecrement': '--',
    'Postincrement': '++',
    'Postdecrement': '--',
}

# Operatori SVA supportati
SVA_IMPLICATION_OPS = {'NonOverlappedImplication', 'OverlappedImplication'}

# SVA System Functions Configuration (Single Source of Truth)
# ... (invariato rispetto alla versione precedente) ...
SVA_SYS_FUNCS = {
    '$past': {
        'has_history': True,
        'dynamic_depth': True,
        'default_depth': 1,
        'template': None 
    },
    '$rose': {
        'has_history': True,
        'dynamic_depth': False,
        'fixed_depth': 1,
        'template': "({0} && !{1})"
    },
    '$fell': {
        'has_history': True,
        'dynamic_depth': False,
        'fixed_depth': 1,
        'template': "(!{0} && {1})"
    },
    '$stable': {
        'has_history': True,
        'dynamic_depth': False,
        'fixed_depth': 1,
        'template': "({0} == {1})"
    },
    '$changed': {
        'has_history': True,
        'dynamic_depth': False,
        'fixed_depth': 1,
        'template': "({0} != {1})"
    }
}