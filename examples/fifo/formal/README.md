# Formal analysis with SymbiYosys

The reference FIFO is checked by bounded model checking. Mutation tests are used to check that the generated immediate assertions detect controlled RTL bugs.

## Source files

The default formal configuration uses bounded model checking with depth 64

`fifo_formal_top.sv` : top-level that instantiates fifo and transpiled assertions and defines the reset sequence

`fifo.sby` : configuration for sby

## Running the reference FIFO

With no mutation enabled, the generated immediate assertions are expected to pass:

```tcl
[script]
read -formal -sv -I. common.sv fifo.sv fifo_assertions_immediate.sv fifo_formal_top.sv
```

Run:

```bash
sby -f fifo.sby
```

## Running mutation tests

The `script` section of `fifo.sby` can be modified to enable one mutation at a time.

For example, to enable the read-data mutation:

```tcl
[script]
read -formal -sv -I. -DMUT_RD_DATA common.sv fifo.sv fifo_assertions_immediate.sv fifo_formal_top.sv
```

With a mutation enabled, the corresponding immediate assertion is expected to fail, and SymbiYosys should produce a counterexample trace.

## Expected result

- Reference FIFO: all enabled immediate assertions pass BMC.
- Mutated FIFO: at least one generated immediate assertion fails, depending on the injected mutation.
