# Lessons Learned

## Missing React State Declarations after Refactoring
- **Issue**: After modifying or moving logic between files/components, variables can be left undeclared (such as `loading`, `simData`, `showCars`, etc.) in the state hooks. This leads to immediate uncaught `ReferenceError` exceptions, causing the React application to render a blank page.
- **Remedy**: Always verify that every variable and event handler used in JSX or functions inside a component is fully defined and declared. Run compile/build checks (`npm run build`) and review code changes systematically to ensure nothing is missing.
